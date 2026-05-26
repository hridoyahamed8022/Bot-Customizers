import TelegramBot from "node-telegram-bot-api";
import { GoogleGenAI } from "@google/genai";
import { logger } from "./lib/logger";

const TOKEN = process.env["TELEGRAM_BOT_TOKEN"];
const ADMIN_CHAT_ID = process.env["ADMIN_CHAT_ID"]
  ? Number(process.env["ADMIN_CHAT_ID"])
  : null;
const GEMINI_API_KEY = process.env["GEMINI_API_KEY"];

if (!TOKEN) throw new Error("TELEGRAM_BOT_TOKEN is required");
if (!GEMINI_API_KEY) throw new Error("GEMINI_API_KEY is required");

const bot = new TelegramBot(TOKEN, { polling: true });
const genai = new GoogleGenAI({ apiKey: GEMINI_API_KEY });

// ─── Constants ────────────────────────────────────────────────────────────────
const MOVIE_BOT = "@moviex_hub_bot";
const ADMIN_USERNAME = "@smartdollarsells";
const DELETE_AFTER_MS = 30_000;
const MAX_WARNINGS = 3;
const FLOOD_LIMIT = 5;
const FLOOD_WINDOW_MS = 5_000;
const STICKER_LIMIT = 3;
const STICKER_WINDOW_MS = 10_000;
const NEW_MEMBER_RESTRICT_MS = 5 * 60 * 1_000;
const AUTO_UNMUTE_MS = 24 * 60 * 60 * 1_000;

// ─── AI System Prompt ─────────────────────────────────────────────────────────
const SYSTEM_PROMPT = `তুমি একটি বাংলা Telegram গ্রুপের সহকারী বট। তোমার নাম "MovieX Hub Bot"।
তুমি সম্পূর্ণ বাংলায় কথা বলবে, মানুষের মতো স্বাভাবিক ও বন্ধুত্বপূর্ণ ভাষায়।

তোমার গ্রুপ সম্পর্কে তথ্য:
- গ্রুপটি মুভি ও নাটক শেয়ারের গ্রুপ
- মুভি/নাটক পেতে ${MOVIE_BOT} বটে ইংরেজি নাম লিখতে হয়
- গ্রুপের পরিচালক: ${ADMIN_USERNAME}
- গ্রুপে লিংক শেয়ার নিষিদ্ধ, স্প্যাম নিষিদ্ধ

তোমার আচরণ:
- সংক্ষিপ্ত ও স্পষ্ট উত্তর দাও (৩-৫ লাইনের মধ্যে)
- বন্ধুত্বপূর্ণ ও সহানুভূতিশীল হও
- কেউ মুভি চাইলে ${MOVIE_BOT} এ পাঠাও
- কেউ ব্যান/মিউট নিয়ে জিজ্ঞেস করলে ${ADMIN_USERNAME} এ পাঠাও
- কেউ সালাম/হ্যালো দিলে সুন্দরভাবে উত্তর দাও
- কোনো প্রশ্ন করলে বিষয়ভিত্তিক সাহায্য করো
- HTML ট্যাগ ব্যবহার করো না, শুধু সাধারণ টেক্সট লেখো
- শেষে সবসময় এই লাইনটি যোগ করো না, শুধু স্বাভাবিকভাবে কথা বলো`;

// ─── Conversation History (per user) ─────────────────────────────────────────
type ChatTurn = { role: "user" | "model"; text: string };
const conversationHistory = new Map<number, ChatTurn[]>();
const MAX_HISTORY = 10;

async function getAIReply(userId: number, userMessage: string): Promise<string> {
  const history = conversationHistory.get(userId) ?? [];

  history.push({ role: "user", text: userMessage });
  if (history.length > MAX_HISTORY * 2) history.splice(0, 2);

  try {
    const contents = history.map((h) => ({
      role: h.role,
      parts: [{ text: h.text }],
    }));

    const response = await genai.models.generateContent({
      model: "gemini-2.5-flash",
      contents,
      config: {
        systemInstruction: SYSTEM_PROMPT,
        maxOutputTokens: 512,
      },
    });

    const reply = response.text?.trim() ?? "দুঃখিত, এই মুহূর্তে উত্তর দিতে পারছি না।";
    history.push({ role: "model", text: reply });
    conversationHistory.set(userId, history);
    return reply;
  } catch (err) {
    logger.error({ err }, "Gemini API error");
    return `দুঃখিত, এই মুহূর্তে উত্তর দিতে পারছি না। সাহায্যের জন্য ${ADMIN_USERNAME} -কে মেসেজ করুন।`;
  }
}

// ─── State ────────────────────────────────────────────────────────────────────
type UserWarning = { count: number; reasons: string[] };
const warnings = new Map<number, UserWarning>();
const mutedUsers = new Set<number>();
const floodData = new Map<number, { count: number; timer: ReturnType<typeof setTimeout> }>();
const stickerData = new Map<number, { count: number; timer: ReturnType<typeof setTimeout> }>();
const newMemberJoinTime = new Map<number, number>();

// ─── Helpers ──────────────────────────────────────────────────────────────────
async function tryDelete(chatId: number, messageId: number) {
  try { await bot.deleteMessage(chatId, messageId); } catch { /* ignored */ }
}

function scheduleDelete(chatId: number, messageId: number, ms = DELETE_AFTER_MS) {
  setTimeout(() => tryDelete(chatId, messageId), ms);
}

async function sendTemp(
  chatId: number,
  text: string,
  extra?: TelegramBot.SendMessageOptions,
): Promise<TelegramBot.Message | undefined> {
  try {
    const msg = await bot.sendMessage(chatId, text, {
      parse_mode: "HTML",
      ...extra,
    });
    scheduleDelete(chatId, msg.message_id);
    return msg;
  } catch (err) {
    logger.error({ err }, "sendTemp failed");
    return undefined;
  }
}

async function isAdmin(chatId: number, userId: number): Promise<boolean> {
  try {
    const m = await bot.getChatMember(chatId, userId);
    return ["administrator", "creator"].includes(m.status);
  } catch { return false; }
}

async function muteUser(chatId: number, userId: number) {
  await bot.restrictChatMember(chatId, userId, {
    permissions: {
      can_send_messages: false,
      can_send_audios: false,
      can_send_documents: false,
      can_send_photos: false,
      can_send_videos: false,
      can_send_video_notes: false,
      can_send_voice_notes: false,
      can_send_polls: false,
      can_send_other_messages: false,
      can_add_web_page_previews: false,
      can_change_info: false,
      can_invite_users: false,
      can_pin_messages: false,
    },
  });
  mutedUsers.add(userId);
  setTimeout(async () => {
    if (!mutedUsers.has(userId)) return;
    try { await unmuteUser(chatId, userId); } catch { /* ignored */ }
  }, AUTO_UNMUTE_MS);
}

async function unmuteUser(chatId: number, userId: number) {
  await bot.restrictChatMember(chatId, userId, {
    permissions: {
      can_send_messages: true,
      can_send_audios: true,
      can_send_documents: true,
      can_send_photos: true,
      can_send_videos: true,
      can_send_video_notes: true,
      can_send_voice_notes: true,
      can_send_polls: true,
      can_send_other_messages: true,
      can_add_web_page_previews: true,
      can_change_info: false,
      can_invite_users: true,
      can_pin_messages: false,
    },
  });
  mutedUsers.delete(userId);
  warnings.delete(userId);
}

// ─── Admin Notification ───────────────────────────────────────────────────────
async function notifyAdmin(
  chatId: number,
  userId: number,
  firstName: string,
  username: string | undefined,
  reason: string,
  action: "muted" | "warned",
) {
  if (!ADMIN_CHAT_ID) return;

  const userTag = username ? `@${username}` : `#${userId}`;
  const actionLabel = action === "muted" ? "🔇 মিউট" : "⚠️ সতর্কতা";
  const warnInfo = warnings.get(userId);
  const warnCount = warnInfo?.count ?? 0;

  const text =
    `${actionLabel} করা হয়েছে!\n\n` +
    `👤 <b>ব্যবহারকারী:</b> ${firstName} (${userTag})\n` +
    `🆔 <b>User ID:</b> <code>${userId}</code>\n` +
    `💬 <b>গ্রুপ ID:</b> <code>${chatId}</code>\n` +
    `📌 <b>কারণ:</b> ${reason}\n` +
    `⚠️ <b>মোট সতর্কতা:</b> ${warnCount}/${MAX_WARNINGS}`;

  const keyboard: TelegramBot.InlineKeyboardButton[][] = action === "muted"
    ? [
        [
          { text: "🔓 আনমিউট", callback_data: `unmute:${chatId}:${userId}` },
          { text: "🚫 ব্যান", callback_data: `ban:${chatId}:${userId}` },
        ],
        [
          { text: "⚠️ সতর্কতা মুছুন", callback_data: `clearwarn:${chatId}:${userId}` },
          { text: "👁 প্রোফাইল দেখুন", url: `tg://user?id=${userId}` },
        ],
      ]
    : [
        [
          { text: "🔇 এখনই মিউট", callback_data: `mute:${chatId}:${userId}` },
          { text: "🚫 ব্যান", callback_data: `ban:${chatId}:${userId}` },
        ],
        [
          { text: "✅ উপেক্ষা করুন", callback_data: `ignore:${chatId}:${userId}` },
          { text: "👁 প্রোফাইল দেখুন", url: `tg://user?id=${userId}` },
        ],
      ];

  try {
    await bot.sendMessage(ADMIN_CHAT_ID, text, {
      parse_mode: "HTML",
      reply_markup: { inline_keyboard: keyboard },
    });
  } catch (err) {
    logger.error({ err }, "Admin notification failed");
  }
}

// ─── Warning System ───────────────────────────────────────────────────────────
async function addWarning(
  chatId: number,
  userId: number,
  firstName: string,
  username: string | undefined,
  reason: string,
): Promise<"warned" | "muted"> {
  const prev = warnings.get(userId) ?? { count: 0, reasons: [] };
  const updated: UserWarning = {
    count: prev.count + 1,
    reasons: [...prev.reasons, reason],
  };
  warnings.set(userId, updated);

  if (updated.count >= MAX_WARNINGS) {
    try { await muteUser(chatId, userId); } catch { /* ignored */ }
    warnings.delete(userId);
    await notifyAdmin(chatId, userId, firstName, username, reason, "muted");
    return "muted";
  }

  await notifyAdmin(chatId, userId, firstName, username, reason, "warned");
  return "warned";
}

// ─── Anti-Spam Checks ─────────────────────────────────────────────────────────
const LINK_RE = /(https?:\/\/|t\.me\/|www\.)[^\s]+/i;
const ARABIC_RE = /[\u0600-\u06FF\u0750-\u077F]{5,}/;
const MENTION_RE = /@\w+/g;

function isFlood(userId: number): boolean {
  const d = floodData.get(userId);
  if (!d) {
    const timer = setTimeout(() => floodData.delete(userId), FLOOD_WINDOW_MS);
    floodData.set(userId, { count: 1, timer });
    return false;
  }
  d.count += 1;
  return d.count > FLOOD_LIMIT;
}

function isStickerSpam(userId: number): boolean {
  const d = stickerData.get(userId);
  if (!d) {
    const timer = setTimeout(() => stickerData.delete(userId), STICKER_WINDOW_MS);
    stickerData.set(userId, { count: 1, timer });
    return false;
  }
  d.count += 1;
  return d.count > STICKER_LIMIT;
}

function isNewMember(userId: number): boolean {
  const joined = newMemberJoinTime.get(userId);
  if (!joined) return false;
  return Date.now() - joined < NEW_MEMBER_RESTRICT_MS;
}

// ─── Spam message templates ───────────────────────────────────────────────────
function warnMsg(firstName: string, reason: string, count: number): string {
  return (
    `╔══════════════════╗\n` +
    `  ⚠️  <b>সতর্কতা</b>\n` +
    `╚══════════════════╝\n\n` +
    `👤 <b>${firstName}</b>\n` +
    `📌 কারণ: ${reason}\n` +
    `🔢 সতর্কতা: ${count}/${MAX_WARNINGS}\n\n` +
    `${count >= MAX_WARNINGS - 1 ? "🚨 <b>পরের বার মিউট হবেন!</b>" : `⚡ আরও ${MAX_WARNINGS - count}টি বাকি`}\n\n` +
    `<i>⏳ ৩০ সেকেন্ড পর মুছে যাবে</i>`
  );
}

function muteMsg(firstName: string, reason: string): string {
  return (
    `╔══════════════════╗\n` +
    `  🔇  <b>মিউট</b>\n` +
    `╚══════════════════╝\n\n` +
    `👤 <b>${firstName}</b> মিউট হয়েছেন।\n` +
    `📌 কারণ: ${reason}\n\n` +
    `🔓 মিউট তুলতে: ${ADMIN_USERNAME}\n` +
    `⏰ ২৪ ঘণ্টা পর অটো আনমিউট\n\n` +
    `<i>⏳ ৩০ সেকেন্ড পর মুছে যাবে</i>`
  );
}

// ─── Keyboard builders ────────────────────────────────────────────────────────
function movieKeyboard(): TelegramBot.InlineKeyboardMarkup {
  return {
    inline_keyboard: [[
      { text: "🎬 মুভি বট খুলুন", url: "https://t.me/moviex_hub_bot" },
    ]],
  };
}

function adminKeyboard(): TelegramBot.InlineKeyboardMarkup {
  return {
    inline_keyboard: [[
      { text: "📩 অ্যাডমিনকে মেসেজ করুন", url: "https://t.me/smartdollarsells" },
    ]],
  };
}

function defaultKeyboard(): TelegramBot.InlineKeyboardMarkup {
  return {
    inline_keyboard: [[
      { text: "🎬 মুভি বট", url: "https://t.me/moviex_hub_bot" },
      { text: "📩 অ্যাডমিন", url: "https://t.me/smartdollarsells" },
    ]],
  };
}

// ─── Welcome / Leave ──────────────────────────────────────────────────────────
bot.on("new_chat_members", async (msg) => {
  await tryDelete(msg.chat.id, msg.message_id);
  const chatId = msg.chat.id;

  for (const member of msg.new_chat_members ?? []) {
    newMemberJoinTime.set(member.id, Date.now());
    const firstName = member.first_name ?? "বন্ধু";

    const text =
      `╔══════════════════╗\n` +
      `  🎉  <b>স্বাগতম!</b>\n` +
      `╚══════════════════╝\n\n` +
      `👋 <b>${firstName}</b>, আমাদের গ্রুপে আপনাকে স্বাগত!\n\n` +
      `🎬 <b>মুভি/নাটক পেতে:</b>\n` +
      `└ ${MOVIE_BOT} -এ ইংরেজি নাম লিখুন\n\n` +
      `📋 <b>গ্রুপের নিয়ম:</b>\n` +
      `└ ❌ লিংক শেয়ার নিষিদ্ধ\n` +
      `└ ❌ স্প্যাম করা নিষিদ্ধ\n` +
      `└ ✅ ভদ্রভাবে কথা বলুন\n\n` +
      `💬 যেকোনো সাহায্যে আমাকে জিজ্ঞেস করুন!\n\n` +
      `<i>⏳ ৩০ সেকেন্ড পর মুছে যাবে</i>`;

    await sendTemp(chatId, text, {
      reply_markup: movieKeyboard(),
    });
  }
});

bot.on("left_chat_member", async (msg) => {
  await tryDelete(msg.chat.id, msg.message_id);
});

// ─── Admin Callback Handler ───────────────────────────────────────────────────
bot.on("callback_query", async (query) => {
  if (!query.data || !query.from || !query.message) return;

  if (ADMIN_CHAT_ID && query.from.id !== ADMIN_CHAT_ID) {
    await bot.answerCallbackQuery(query.id, { text: "❌ শুধু অ্যাডমিন ব্যবহার করতে পারবেন।" });
    return;
  }

  const parts = query.data.split(":");
  const action = parts[0];
  const chatId = Number(parts[1]);
  const userId = Number(parts[2]);
  const msgId = query.message.message_id;
  const adminChatId = query.message.chat.id;

  let answer = "";

  try {
    switch (action) {
      case "unmute":
        await unmuteUser(chatId, userId);
        answer = "✅ আনমিউট সম্পন্ন।";
        await bot.editMessageReplyMarkup(
          { inline_keyboard: [[{ text: "✅ আনমিউট করা হয়েছে", callback_data: "done" }]] },
          { chat_id: adminChatId, message_id: msgId },
        );
        break;

      case "mute":
        await muteUser(chatId, userId);
        answer = "🔇 মিউট সম্পন্ন।";
        await bot.editMessageReplyMarkup(
          { inline_keyboard: [[{ text: "🔇 মিউট করা হয়েছে", callback_data: "done" }]] },
          { chat_id: adminChatId, message_id: msgId },
        );
        break;

      case "ban":
        await bot.banChatMember(chatId, userId);
        mutedUsers.delete(userId);
        warnings.delete(userId);
        answer = "🚫 ব্যান সম্পন্ন।";
        await bot.editMessageReplyMarkup(
          { inline_keyboard: [[{ text: "🚫 ব্যান করা হয়েছে", callback_data: "done" }]] },
          { chat_id: adminChatId, message_id: msgId },
        );
        break;

      case "clearwarn":
        warnings.delete(userId);
        answer = "✅ সতর্কতা মুছে দেওয়া হয়েছে।";
        await bot.editMessageReplyMarkup(
          { inline_keyboard: [[{ text: "✅ সতর্কতা মুছে দেওয়া হয়েছে", callback_data: "done" }]] },
          { chat_id: adminChatId, message_id: msgId },
        );
        break;

      case "ignore":
        answer = "✅ উপেক্ষা করা হয়েছে।";
        await bot.editMessageReplyMarkup(
          { inline_keyboard: [[{ text: "✅ উপেক্ষা করা হয়েছে", callback_data: "done" }]] },
          { chat_id: adminChatId, message_id: msgId },
        );
        break;

      case "done":
        answer = "ইতিমধ্যে সম্পন্ন।";
        break;
    }
  } catch (err) {
    logger.error({ err }, "Callback action failed");
    answer = "❌ সমস্যা হয়েছে, আবার চেষ্টা করুন।";
  }

  await bot.answerCallbackQuery(query.id, { text: answer });
});

// ─── Message Handler ──────────────────────────────────────────────────────────
bot.on("message", async (msg) => {
  if (msg.chat.type === "private") return;

  const chatId = msg.chat.id;
  const userId = msg.from?.id;
  const msgId = msg.message_id;
  const firstName = msg.from?.first_name ?? "বন্ধু";
  const username = msg.from?.username;

  if (!userId) return;

  const isAdminUser = await isAdmin(chatId, userId);

  // ── Sticker spam ──────────────────────────────────────────────────────────
  if (msg.sticker && !isAdminUser) {
    if (isStickerSpam(userId)) {
      await tryDelete(chatId, msgId);
      const result = await addWarning(chatId, userId, firstName, username, "স্টিকার স্প্যাম");
      const t = result === "muted"
        ? muteMsg(firstName, "অতিরিক্ত স্টিকার")
        : warnMsg(firstName, "অতিরিক্ত স্টিকার নিষিদ্ধ", warnings.get(userId)?.count ?? 1);
      await sendTemp(chatId, t);
    }
    return;
  }

  // ── Forwarded from channel/bot ────────────────────────────────────────────
  if ((msg.forward_from_chat || msg.forward_from?.is_bot) && !isAdminUser) {
    await tryDelete(chatId, msgId);
    const result = await addWarning(chatId, userId, firstName, username, "চ্যানেল/বট ফরওয়ার্ড");
    const t = result === "muted"
      ? muteMsg(firstName, "চ্যানেল ফরওয়ার্ড করা")
      : warnMsg(firstName, "চ্যানেল/বট ফরওয়ার্ড নিষিদ্ধ", warnings.get(userId)?.count ?? 1);
    await sendTemp(chatId, t);
    return;
  }

  const text = msg.text ?? msg.caption ?? "";
  if (!text) return;

  // ── Flood control ─────────────────────────────────────────────────────────
  if (!isAdminUser && isFlood(userId)) {
    await tryDelete(chatId, msgId);
    if (!mutedUsers.has(userId)) {
      const result = await addWarning(chatId, userId, firstName, username, "মেসেজ ফ্লাড");
      const t = result === "muted"
        ? muteMsg(firstName, "অতিরিক্ত মেসেজ স্প্যাম")
        : warnMsg(firstName, "অল্প সময়ে অনেক মেসেজ নিষিদ্ধ", warnings.get(userId)?.count ?? 1);
      await sendTemp(chatId, t);
    }
    return;
  }

  // ── Link filter ───────────────────────────────────────────────────────────
  if (LINK_RE.test(text) && !isAdminUser) {
    await tryDelete(chatId, msgId);
    if (!mutedUsers.has(userId)) {
      const reason = isNewMember(userId)
        ? "নতুন মেম্বার হিসেবে লিংক পাঠানো নিষিদ্ধ"
        : "লিংক শেয়ার নিষিদ্ধ";
      const result = await addWarning(chatId, userId, firstName, username, reason);
      const t = result === "muted"
        ? muteMsg(firstName, reason)
        : warnMsg(firstName, reason, warnings.get(userId)?.count ?? 1);
      await sendTemp(chatId, t);
    }
    return;
  }

  // ── Mention spam ──────────────────────────────────────────────────────────
  const mentions = text.match(MENTION_RE);
  if (mentions && mentions.length >= 3 && !isAdminUser) {
    await tryDelete(chatId, msgId);
    if (!mutedUsers.has(userId)) {
      const result = await addWarning(chatId, userId, firstName, username, "মেনশন স্প্যাম");
      const t = result === "muted"
        ? muteMsg(firstName, "একসাথে অনেককে ট্যাগ করা")
        : warnMsg(firstName, "৩+ জনকে একসাথে ট্যাগ নিষিদ্ধ", warnings.get(userId)?.count ?? 1);
      await sendTemp(chatId, t);
    }
    return;
  }

  // ── CAPS spam ─────────────────────────────────────────────────────────────
  const letters = text.replace(/[^a-zA-Z]/g, "");
  if (letters.length > 10 && letters === letters.toUpperCase() && !isAdminUser) {
    await tryDelete(chatId, msgId);
    if (!mutedUsers.has(userId)) {
      const result = await addWarning(chatId, userId, firstName, username, "CAPS LOCK স্প্যাম");
      const t = result === "muted"
        ? muteMsg(firstName, "সম্পূর্ণ বড় হাতে লেখা")
        : warnMsg(firstName, "বড় হাতে (CAPS) লেখা নিষিদ্ধ", warnings.get(userId)?.count ?? 1);
      await sendTemp(chatId, t);
    }
    return;
  }

  // ── Arabic/foreign script spam ────────────────────────────────────────────
  if (ARABIC_RE.test(text) && !isAdminUser) {
    await tryDelete(chatId, msgId);
    if (!mutedUsers.has(userId)) {
      const result = await addWarning(chatId, userId, firstName, username, "আরবি/বিদেশি ভাষা স্প্যাম");
      const t = result === "muted"
        ? muteMsg(firstName, "অপরিচিত ভাষায় স্প্যাম")
        : warnMsg(firstName, "আরবি/অপরিচিত ভাষায় মেসেজ নিষিদ্ধ", warnings.get(userId)?.count ?? 1);
      await sendTemp(chatId, t);
    }
    return;
  }

  // ── AI Smart Reply ────────────────────────────────────────────────────────
  if (mutedUsers.has(userId)) return;

  // Determine keyboard based on message content
  const lowerText = text.toLowerCase();
  let keyboard = defaultKeyboard();
  if (/মুভি|movie|নাটক|drama|series|film|ডাউনলোড|সিনেমা/i.test(lowerText)) {
    keyboard = movieKeyboard();
  } else if (/ব্যান|ban|block|মিউট|mute|kick|আনব্যান|unban/i.test(lowerText)) {
    keyboard = adminKeyboard();
  }

  // Get AI reply
  const aiReply = await getAIReply(userId, text);

  await sendTemp(chatId, aiReply, {
    reply_to_message_id: msgId,
    reply_markup: keyboard,
  });
});

// ─── Polling Error ────────────────────────────────────────────────────────────
bot.on("polling_error", (err) => {
  logger.error({ err }, "Telegram polling error");
});

logger.info("Telegram bot started ✅");

export { bot };
