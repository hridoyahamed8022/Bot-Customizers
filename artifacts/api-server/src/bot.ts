import TelegramBot from "node-telegram-bot-api";
import { logger } from "./lib/logger";

const TOKEN = process.env["TELEGRAM_BOT_TOKEN"];

if (!TOKEN) {
  throw new Error("TELEGRAM_BOT_TOKEN is required");
}

const bot = new TelegramBot(TOKEN, { polling: true });

const MOVIE_BOT = "@moviex_hub_bot";
const ADMIN_USERNAME = "@smartdollarsells";
const DELETE_AFTER_MS = 30_000;
const WARNING_LIMIT = 1;

const warnCount = new Map<number, number>();
const mutedUsers = new Set<number>();

async function deleteAfter(chatId: number, messageId: number) {
  setTimeout(async () => {
    try {
      await bot.deleteMessage(chatId, messageId);
    } catch {
    }
  }, DELETE_AFTER_MS);
}

async function sendAndScheduleDelete(
  chatId: number,
  text: string,
  options?: TelegramBot.SendMessageOptions
): Promise<TelegramBot.Message | undefined> {
  try {
    const sent = await bot.sendMessage(chatId, text, {
      parse_mode: "HTML",
      ...options,
    });
    deleteAfter(chatId, sent.message_id);
    return sent;
  } catch (err) {
    logger.error({ err }, "Failed to send message");
    return undefined;
  }
}

async function isAdmin(chatId: number, userId: number): Promise<boolean> {
  try {
    const member = await bot.getChatMember(chatId, userId);
    return ["administrator", "creator"].includes(member.status);
  } catch {
    return false;
  }
}

bot.on("new_chat_members", async (msg) => {
  const chatId = msg.chat.id;
  const newMembers = msg.new_chat_members ?? [];

  try {
    await bot.deleteMessage(chatId, msg.message_id);
  } catch {
  }

  for (const member of newMembers) {
    const firstName = member.first_name ?? "বন্ধু";

    const welcomeText =
      `👋 <b>স্বাগতম, ${firstName}!</b>\n\n` +
      `আমাদের গ্রুপে আপনাকে আন্তরিকভাবে স্বাগত জানাই। 🎉\n\n` +
      `🎬 <b>মুভি ও নাটক খুঁজছেন?</b>\n` +
      `আমাদের বট ${MOVIE_BOT} -এ সকল মুভি ও নাটক পাওয়া যায়!\n` +
      `বটে গিয়ে শুধু <b>সঠিক ইংরেজি নাম</b> লিখে পাঠান — তাহলেই পেয়ে যাবেন। ✅\n\n` +
      `📌 <b>গ্রুপের নিয়মকানুন:</b>\n` +
      `• গ্রুপে কোনো লিংক শেয়ার করবেন না\n` +
      `• সবার সাথে ভদ্রভাবে কথা বলুন\n\n` +
      `❓ কোনো সাহায্য লাগলে মেসেজ করুন — আমি সাহায্য করতে প্রস্তুত!\n\n` +
      `<i>(এই মেসেজটি ৩০ সেকেন্ড পর মুছে যাবে)</i>`;

    await sendAndScheduleDelete(chatId, welcomeText);
  }
});

bot.on("left_chat_member", async (msg) => {
  try {
    await bot.deleteMessage(msg.chat.id, msg.message_id);
  } catch {
  }
});

function detectIntent(text: string): "ban" | "movie" | "help" | "greeting" | "other" {
  const lower = text.toLowerCase();

  const banKeywords = [
    "ব্যান", "ban", "banned", "বাধা", "block", "ব্লক", "kick", "কিক",
    "আনব্যান", "unban", "মুক্ত", "ছাড়", "আনব্লক", "unblock",
    "রিমুভ", "remove", "বের করে", "বাদ দিয়েছে",
  ];

  const movieKeywords = [
    "মুভি", "movie", "নাটক", "drama", "সিরিজ", "series", "film", "ফিল্ম",
    "ডাউনলোড", "download", "link", "লিংক", "পাবো", "পাই", "কোথায়",
  ];

  const helpKeywords = [
    "সাহায্য", "help", "হেল্প", "সমস্যা", "problem", "issue",
    "বুঝতে", "জানতে", "কীভাবে", "কিভাবে", "how", "কি করবো",
    "কি করব", "কী করবো", "বলুন", "জানান",
  ];

  const greetingKeywords = [
    "হ্যালো", "hello", "hi", "হাই", "আস্সালামু", "সালাম", "ওহে",
    "কেমন আছেন", "কেমন আছো", "কি খবর",
  ];

  if (banKeywords.some((k) => lower.includes(k))) return "ban";
  if (movieKeywords.some((k) => lower.includes(k))) return "movie";
  if (greetingKeywords.some((k) => lower.includes(k))) return "greeting";
  if (helpKeywords.some((k) => lower.includes(k))) return "help";
  return "other";
}

function buildReply(intent: "ban" | "movie" | "help" | "greeting" | "other", firstName: string): string | null {
  switch (intent) {
    case "ban":
      return (
        `😔 <b>${firstName}</b>, আপনার ব্যান বা রেস্ট্রিকশন সংক্রান্ত সমস্যার জন্য দুঃখিত!\n\n` +
        `🔓 <b>ব্যান তোলার অনুরোধ করতে:</b>\n` +
        `সরাসরি আমাদের পরিচালক <b>${ADMIN_USERNAME}</b> -কে মেসেজ করুন।\n\n` +
        `মেসেজে আপনার সমস্যার বিবরণ জানান — তিনি যত তাড়াতাড়ি সম্ভব সাহায্য করবেন। ✅\n\n` +
        `<i>(এই মেসেজটি ৩০ সেকেন্ড পর মুছে যাবে)</i>`
      );

    case "movie":
      return (
        `🎬 <b>${firstName}</b>, মুভি বা নাটক খুঁজছেন?\n\n` +
        `আমাদের বট ${MOVIE_BOT} -এ সকল মুভি ও নাটক পাওয়া যায়!\n\n` +
        `📌 <b>কীভাবে খুঁজবেন:</b>\n` +
        `বটে গিয়ে শুধু <b>সঠিক ইংরেজি নাম</b> লিখে পাঠান।\n` +
        `উদাহরণ: <code>Avengers Endgame</code>\n\n` +
        `<i>(এই মেসেজটি ৩০ সেকেন্ড পর মুছে যাবে)</i>`
      );

    case "greeting":
      return (
        `👋 <b>হ্যালো, ${firstName}!</b>\n\n` +
        `আমি এই গ্রুপের সহকারী বট। আমি আপনাকে সাহায্য করতে পারি:\n\n` +
        `🎬 মুভি/নাটক খুঁজতে → ${MOVIE_BOT}\n` +
        `🔓 ব্যান সংক্রান্ত → ${ADMIN_USERNAME}\n` +
        `❓ যেকোনো সমস্যায় → আমাকে জিজ্ঞেস করুন!\n\n` +
        `<i>(এই মেসেজটি ৩০ সেকেন্ড পর মুছে যাবে)</i>`
      );

    case "help":
      return (
        `🤝 <b>${firstName}</b>, আমি কীভাবে সাহায্য করতে পারি?\n\n` +
        `আমি এই বিষয়গুলোতে সাহায্য করতে পারি:\n\n` +
        `🎬 <b>মুভি/নাটক:</b> ${MOVIE_BOT} -এ ইংরেজি নাম লিখে খুঁজুন\n` +
        `🔓 <b>ব্যান সমস্যা:</b> ${ADMIN_USERNAME} -কে মেসেজ করুন\n` +
        `📋 <b>গ্রুপের নিয়ম:</b> গ্রুপে লিংক শেয়ার করা যাবে না\n\n` +
        `আপনার সমস্যার কথা বিস্তারিত জানান — আমি যথাসাধ্য সাহায্য করব! ✅\n\n` +
        `<i>(এই মেসেজটি ৩০ সেকেন্ড পর মুছে যাবে)</i>`
      );

    default:
      return null;
  }
}

const LINK_REGEX = /(https?:\/\/|t\.me\/|www\.)[^\s]+/i;

bot.on("message", async (msg) => {
  if (msg.chat.type === "private") return;

  const chatId = msg.chat.id;
  const userId = msg.from?.id;
  const messageId = msg.message_id;
  const firstName = msg.from?.first_name ?? "বন্ধু";

  if (!userId) return;

  const text = msg.text ?? msg.caption ?? "";

  if (!text) return;

  const userIsAdmin = await isAdmin(chatId, userId);

  if (LINK_REGEX.test(text) && !userIsAdmin) {
    try {
      await bot.deleteMessage(chatId, messageId);
    } catch {
    }

    if (mutedUsers.has(userId)) return;

    const currentWarnings = (warnCount.get(userId) ?? 0) + 1;
    warnCount.set(userId, currentWarnings);

    if (currentWarnings <= WARNING_LIMIT) {
      const warnText =
        `⚠️ <b>${firstName}</b>, গ্রুপে লিংক শেয়ার করা যাবে না!\n\n` +
        `এটি আপনার <b>${currentWarnings}/${WARNING_LIMIT + 1}</b> নম্বর সতর্কতা।\n` +
        `পরবর্তীবার লিংক পাঠালে আপনি মেসেজ পাঠানোর অনুমতি হারাবেন।\n\n` +
        `🎬 মুভি বা নাটক চাইলে ${MOVIE_BOT} -এ সরাসরি ইংরেজি নাম লিখে খুঁজুন।\n\n` +
        `<i>(এই মেসেজটি ৩০ সেকেন্ড পর মুছে যাবে)</i>`;

      await sendAndScheduleDelete(chatId, warnText);
    } else {
      mutedUsers.add(userId);
      warnCount.delete(userId);

      try {
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
      } catch (err) {
        logger.error({ err }, "Failed to restrict user");
      }

      const muteText =
        `🚫 <b>${firstName}</b> কে মিউট করা হয়েছে!\n\n` +
        `একাধিকবার লিংক শেয়ার করার কারণে আপনার মেসেজ পাঠানোর অনুমতি বাতিল করা হয়েছে।\n` +
        `ব্যান তুলতে ${ADMIN_USERNAME} -কে মেসেজ করুন।\n\n` +
        `<i>(এই মেসেজটি ৩০ সেকেন্ড পর মুছে যাবে)</i>`;

      await sendAndScheduleDelete(chatId, muteText);
    }

    return;
  }

  const intent = detectIntent(text);
  const reply = buildReply(intent, firstName);

  if (reply) {
    await sendAndScheduleDelete(chatId, reply, {
      reply_to_message_id: messageId,
    });
  }
});

bot.on("polling_error", (err) => {
  logger.error({ err }, "Telegram polling error");
});

logger.info("Telegram bot started");

export { bot };
