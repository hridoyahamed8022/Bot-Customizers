import TelegramBot from "node-telegram-bot-api";
import { logger } from "./lib/logger";

const TOKEN = process.env["TELEGRAM_BOT_TOKEN"];
const ADMIN_CHAT_ID = process.env["ADMIN_CHAT_ID"]
  ? Number(process.env["ADMIN_CHAT_ID"])
  : null;

if (!TOKEN) throw new Error("TELEGRAM_BOT_TOKEN is required");

const bot = new TelegramBot(TOKEN, { polling: true });

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

async function deleteAfter(chatId: number, messageId: number, ms = DELETE_AFTER_MS) {
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
    deleteAfter(chatId, msg.message_id);
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
          { text: "👁 প্রোফাইল", url: `tg://user?id=${userId}` },
        ],
      ]
    : [
        [
          { text: "🔇 এখনই মিউট", callback_data: `mute:${chatId}:${userId}` },
          { text: "🚫 ব্যান", callback_data: `ban:${chatId}:${userId}` },
        ],
        [
          { text: "✅ উপেক্ষা করুন", callback_data: `ignore:${chatId}:${userId}` },
          { text: "👁 প্রোফাইল", url: `tg://user?id=${userId}` },
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
  if (d.count > FLOOD_LIMIT) return true;
  return false;
}

function isStickerSpam(userId: number): boolean {
  const d = stickerData.get(userId);
  if (!d) {
    const timer = setTimeout(() => stickerData.delete(userId), STICKER_WINDOW_MS);
    stickerData.set(userId, { count: 1, timer });
    return false;
  }
  d.count += 1;
  if (d.count > STICKER_LIMIT) return true;
  return false;
}

function isNewMember(userId: number): boolean {
  const joined = newMemberJoinTime.get(userId);
  if (!joined) return false;
  return Date.now() - joined < NEW_MEMBER_RESTRICT_MS;
}

// ─── Modern Messages ──────────────────────────────────────────────────────────
function warnMsg(firstName: string, reason: string, count: number): string {
  return (
    `╔══════════════════════╗\n` +
    `  ⚠️  <b>সতর্কতা বার্তা</b>\n` +
    `╚══════════════════════╝\n\n` +
    `👤 <b>${firstName}</b>\n` +
    `📌 <b>কারণ:</b> ${reason}\n` +
    `🔢 <b>সতর্কতা:</b> ${count}/${MAX_WARNINGS}\n\n` +
    `${count >= MAX_WARNINGS - 1
      ? "🚨 <b>পরবর্তী লঙ্ঘনে আপনি মিউট হবেন!</b>"
      : `⚡ আরও ${MAX_WARNINGS - count}টি সতর্কতা বাকি`}\n\n` +
    `<i>⏳ এই বার্তা ৩০ সেকেন্ড পর মুছে যাবে</i>`
  );
}

function muteMsg(firstName: string, reason: string): string {
  return (
    `╔══════════════════════╗\n` +
    `  🔇  <b>মিউট করা হয়েছে</b>\n` +
    `╚══════════════════════╝\n\n` +
    `👤 <b>${firstName}</b> কে মিউট করা হয়েছে।\n` +
    `📌 <b>কারণ:</b> ${reason}\n\n` +
    `🔓 <b>আনমিউট হতে চাইলে:</b>\n` +
    `${ADMIN_USERNAME} -কে মেসেজ করুন\n\n` +
    `⏰ <i>২৪ ঘণ্টা পর স্বয়ংক্রিয় আনমিউট হবে</i>\n` +
    `<i>⏳ এই বার্তা ৩০ সেকেন্ড পর মুছে যাবে</i>`
  );
}

function helpMsg(firstName: string): string {
  return (
    `╔══════════════════════╗\n` +
    `  🤖  <b>MovieX Hub Bot</b>\n` +
    `╚══════════════════════╝\n\n` +
    `👋 হ্যালো <b>${firstName}</b>! আমি কীভাবে সাহায্য করতে পারি?\n\n` +
    `🎬 <b>মুভি/নাটক পেতে:</b>\n` +
    `└ ${MOVIE_BOT} -এ ইংরেজি নাম লিখুন\n\n` +
    `🔓 <b>ব্যান/মিউট সমস্যায়:</b>\n` +
    `└ ${ADMIN_USERNAME} -কে মেসেজ করুন\n\n` +
    `📋 <b>গ্রুপের নিয়ম:</b>\n` +
    `└ লিংক শেয়ার নিষিদ্ধ\n` +
    `└ স্প্যাম করা যাবে না\n` +
    `└ বড় হাতে লেখা নিষিদ্ধ\n\n` +
    `<i>⏳ এই বার্তা ৩০ সেকেন্ড পর মুছে যাবে</i>`
  );
}

function movieMsg(firstName: string): string {
  return (
    `╔══════════════════════╗\n` +
    `  🎬  <b>মুভি/নাটক খুঁজুন</b>\n` +
    `╚══════════════════════╝\n\n` +
    `👤 <b>${firstName}</b>, আপনি মুভি বা নাটক খুঁজছেন?\n\n` +
    `✅ <b>কীভাবে পাবেন:</b>\n` +
    `└ ${MOVIE_BOT} -এ যান\n` +
    `└ সঠিক <b>ইংরেজি নাম</b> লিখুন\n` +
    `└ উদাহরণ: <code>Avengers Endgame</code>\n\n` +
    `<i>⏳ এই বার্তা ৩০ সেকেন্ড পর মুছে যাবে</i>`
  );
}

function banMsg(firstName: string): string {
  return (
    `╔══════════════════════╗\n` +
    `  🔓  <b>ব্যান/মিউট সমস্যা</b>\n` +
    `╚══════════════════════╝\n\n` +
    `😔 <b>${firstName}</b>, আপনার সমস্যার কথা জানতে পারলাম।\n\n` +
    `📩 <b>সমাধান পেতে:</b>\n` +
    `└ ${ADMIN_USERNAME} -কে সরাসরি মেসেজ করুন\n` +
    `└ আপনার User ID: <code>দিন</code>\n` +
    `└ সমস্যার বিবরণ লিখুন\n\n` +
    `⏰ <i>অ্যাডমিন যত তাড়াতাড়ি সম্ভব সমাধান করবেন।</i>\n\n` +
    `<i>⏳ এই বার্তা ৩০ সেকেন্ড পর মুছে যাবে</i>`
  );
}

function greetMsg(firstName: string): string {
  return (
    `╔══════════════════════╗\n` +
    `  👋  <b>MovieX Hub Bot</b>\n` +
    `╚══════════════════════╝\n\n` +
    `হ্যালো <b>${firstName}</b>! 😊\n\n` +
    `আমি এই গ্রুপের সহকারী বট।\n` +
    `আপনার যেকোনো সাহায্যে আমি প্রস্তুত!\n\n` +
    `<i>⏳ এই বার্তা ৩০ সেকেন্ড পর মুছে যাবে</i>`
  );
}

function defaultMsg(firstName: string): string {
  return (
    `╔══════════════════════╗\n` +
    `  🤖  <b>MovieX Hub Bot</b>\n` +
    `╚══════════════════════╝\n\n` +
    `👤 <b>${firstName}</b>, আপনার মেসেজ পেয়েছি!\n\n` +
    `আমি যে বিষয়গুলোতে সাহায্য করতে পারি:\n\n` +
    `🎬 মুভি/নাটক → ${MOVIE_BOT}\n` +
    `🔓 ব্যান/মিউট → ${ADMIN_USERNAME}\n` +
    `❓ যেকোনো সমস্যা → ${ADMIN_USERNAME}\n\n` +
    `<i>⏳ এই বার্তা ৩০ সেকেন্ড পর মুছে যাবে</i>`
  );
}

// ─── Intent Detection ─────────────────────────────────────────────────────────
type Intent = "ban" | "movie" | "help" | "greeting" | "other";

function detectIntent(text: string): Intent {
  const t = text.toLowerCase();
  if (/ব্যান|ban|banned|block|ব্লক|kick|আনব্যান|unban|মিউট|mute|বাধা|রিমুভ|remove/i.test(t)) return "ban";
  if (/মুভি|movie|নাটক|drama|series|film|ফিল্ম|ডাউনলোড|download|পাবো|কোথায়|সিনেমা/i.test(t)) return "movie";
  if (/হ্যালো|hello|hi|হাই|সালাম|ওহে|কেমন আছ|কি খবর/i.test(t)) return "greeting";
  if (/সাহায্য|help|হেল্প|সমস্যা|problem|issue|কীভাবে|কিভাবে|বলুন|জানান/i.test(t)) return "help";
  return "other";
}

function buildReplyText(intent: Intent, firstName: string): string {
  switch (intent) {
    case "ban": return banMsg(firstName);
    case "movie": return movieMsg(firstName);
    case "greeting": return greetMsg(firstName);
    case "help": return helpMsg(firstName);
    default: return defaultMsg(firstName);
  }
}

function buildReplyKeyboard(intent: Intent): TelegramBot.InlineKeyboardMarkup {
  switch (intent) {
    case "movie":
      return {
        inline_keyboard: [[
          { text: "🎬 মুভি বট খুলুন", url: `https://t.me/moviex_hub_bot` },
        ]],
      };
    case "ban":
      return {
        inline_keyboard: [[
          { text: "📩 অ্যাডমিনকে মেসেজ করুন", url: `https://t.me/smartdollarsells` },
        ]],
      };
    default:
      return {
        inline_keyboard: [[
          { text: "🎬 মুভি বট", url: `https://t.me/moviex_hub_bot` },
          { text: "📩 অ্যাডমিন", url: `https://t.me/smartdollarsells` },
        ]],
      };
  }
}

// ─── Welcome / Leave ─────────────────────────────────────────────────────────
bot.on("new_chat_members", async (msg) => {
  await tryDelete(msg.chat.id, msg.message_id);
  const chatId = msg.chat.id;

  for (const member of msg.new_chat_members ?? []) {
    newMemberJoinTime.set(member.id, Date.now());
    const firstName = member.first_name ?? "বন্ধু";

    const text =
      `╔══════════════════════╗\n` +
      `  🎉  <b>নতুন সদস্য!</b>\n` +
      `╚══════════════════════╝\n\n` +
      `👋 <b>স্বাগতম, ${firstName}!</b>\n\n` +
      `আমাদের গ্রুপে আপনাকে আন্তরিকভাবে স্বাগত জানাই। 🌟\n\n` +
      `🎬 <b>মুভি ও নাটক পেতে:</b>\n` +
      `└ ${MOVIE_BOT} -এ ইংরেজি নাম লিখুন\n\n` +
      `📋 <b>গ্রুপের নিয়ম:</b>\n` +
      `└ ❌ লিংক শেয়ার নিষিদ্ধ\n` +
      `└ ❌ স্প্যাম করা যাবে না\n` +
      `└ ✅ সবার সাথে ভদ্রভাবে কথা বলুন\n\n` +
      `<i>⏳ এই বার্তা ৩০ সেকেন্ড পর মুছে যাবে</i>`;

    await sendTemp(chatId, text, {
      reply_markup: {
        inline_keyboard: [[
          { text: "🎬 মুভি বট খুলুন", url: "https://t.me/moviex_hub_bot" },
        ]],
      },
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

  const [action, rawChatId, rawUserId] = query.data.split(":");
  const chatId = Number(rawChatId);
  const userId = Number(rawUserId);
  const msgId = query.message.message_id;
  const adminChatId = query.message.chat.id;

  let answer = "";

  try {
    switch (action) {
      case "unmute":
        await unmuteUser(chatId, userId);
        answer = "✅ ব্যবহারকারীকে আনমিউট করা হয়েছে।";
        await bot.editMessageReplyMarkup(
          { inline_keyboard: [[{ text: "✅ আনমিউট করা হয়েছে", callback_data: "done" }]] },
          { chat_id: adminChatId, message_id: msgId }
        );
        break;

      case "mute":
        await muteUser(chatId, userId);
        answer = "🔇 ব্যবহারকারীকে মিউট করা হয়েছে।";
        await bot.editMessageReplyMarkup(
          { inline_keyboard: [[{ text: "🔇 মিউট করা হয়েছে", callback_data: "done" }]] },
          { chat_id: adminChatId, message_id: msgId }
        );
        break;

      case "ban":
        await bot.banChatMember(chatId, userId);
        mutedUsers.delete(userId);
        warnings.delete(userId);
        answer = "🚫 ব্যবহারকারীকে ব্যান করা হয়েছে।";
        await bot.editMessageReplyMarkup(
          { inline_keyboard: [[{ text: "🚫 ব্যান করা হয়েছে", callback_data: "done" }]] },
          { chat_id: adminChatId, message_id: msgId }
        );
        break;

      case "clearwarn":
        warnings.delete(userId);
        answer = "✅ সতর্কতা মুছে দেওয়া হয়েছে।";
        await bot.editMessageReplyMarkup(
          { inline_keyboard: [[{ text: "✅ সতর্কতা মুছে দেওয়া হয়েছে", callback_data: "done" }]] },
          { chat_id: adminChatId, message_id: msgId }
        );
        break;

      case "ignore":
        answer = "✅ উপেক্ষা করা হয়েছে।";
        await bot.editMessageReplyMarkup(
          { inline_keyboard: [[{ text: "✅ উপেক্ষা করা হয়েছে", callback_data: "done" }]] },
          { chat_id: adminChatId, message_id: msgId }
        );
        break;

      case "done":
        answer = "ইতিমধ্যে সম্পন্ন হয়েছে।";
        break;
    }
  } catch (err) {
    logger.error({ err }, "Callback action failed");
    answer = "❌ কাজটি সম্পন্ন করতে সমস্যা হয়েছে।";
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
      const text = result === "muted"
        ? muteMsg(firstName, "অতিরিক্ত স্টিকার পাঠানো")
        : warnMsg(firstName, "অতিরিক্ত স্টিকার পাঠানো", warnings.get(userId)?.count ?? 1);
      await sendTemp(chatId, text);
    }
    return;
  }

  // ── Forwarded from channel/bot ────────────────────────────────────────────
  if ((msg.forward_from_chat || msg.forward_from?.is_bot) && !isAdminUser) {
    await tryDelete(chatId, msgId);
    const result = await addWarning(chatId, userId, firstName, username, "চ্যানেল/বট থেকে ফরওয়ার্ড");
    const text = result === "muted"
      ? muteMsg(firstName, "চ্যানেল/বট থেকে ফরওয়ার্ড করা মেসেজ")
      : warnMsg(firstName, "চ্যানেল/বট থেকে ফরওয়ার্ড নিষিদ্ধ", warnings.get(userId)?.count ?? 1);
    await sendTemp(chatId, text);
    return;
  }

  const text = msg.text ?? msg.caption ?? "";
  if (!text) return;

  // ── Flood control ─────────────────────────────────────────────────────────
  if (!isAdminUser && isFlood(userId)) {
    await tryDelete(chatId, msgId);
    if (!mutedUsers.has(userId)) {
      const result = await addWarning(chatId, userId, firstName, username, "মেসেজ ফ্লাড/স্প্যাম");
      const t = result === "muted"
        ? muteMsg(firstName, "অতিরিক্ত মেসেজ স্প্যাম")
        : warnMsg(firstName, "অতিরিক্ত মেসেজ পাঠানো নিষিদ্ধ", warnings.get(userId)?.count ?? 1);
      await sendTemp(chatId, t);
    }
    return;
  }

  // ── Link filter ───────────────────────────────────────────────────────────
  if (LINK_RE.test(text) && !isAdminUser) {
    await tryDelete(chatId, msgId);
    if (!mutedUsers.has(userId)) {
      const reason = isNewMember(userId) ? "নতুন মেম্বার হিসেবে লিংক পাঠানো নিষিদ্ধ" : "লিংক শেয়ার নিষিদ্ধ";
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
        : warnMsg(firstName, "একসাথে ৩+ জনকে ট্যাগ করা নিষিদ্ধ", warnings.get(userId)?.count ?? 1);
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

  // ── Arabic / foreign script spam ─────────────────────────────────────────
  if (ARABIC_RE.test(text) && !isAdminUser) {
    await tryDelete(chatId, msgId);
    if (!mutedUsers.has(userId)) {
      const result = await addWarning(chatId, userId, firstName, username, "বিদেশি ভাষায় স্প্যাম");
      const t = result === "muted"
        ? muteMsg(firstName, "অপরিচিত/আরবি ভাষায় স্প্যাম")
        : warnMsg(firstName, "আরবি/অপরিচিত ভাষায় মেসেজ নিষিদ্ধ", warnings.get(userId)?.count ?? 1);
      await sendTemp(chatId, t);
    }
    return;
  }

  // ── Smart reply ───────────────────────────────────────────────────────────
  if (mutedUsers.has(userId)) return;

  const intent = detectIntent(text);
  const replyText = buildReplyText(intent, firstName);
  const keyboard = buildReplyKeyboard(intent);

  await sendTemp(chatId, replyText, {
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
