import TelegramBot from "node-telegram-bot-api";
import { logger } from "./lib/logger";

const TOKEN = process.env["TELEGRAM_BOT_TOKEN"];

if (!TOKEN) {
  throw new Error("TELEGRAM_BOT_TOKEN is required");
}

const bot = new TelegramBot(TOKEN, { polling: true });

const MOVIE_BOT = "@moviex_hub_bot";
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

bot.on("new_chat_members", async (msg) => {
  const chatId = msg.chat.id;
  const newMembers = msg.new_chat_members ?? [];

  for (const member of newMembers) {
    const firstName = member.first_name ?? "বন্ধু";
    const username = member.username ? `@${member.username}` : firstName;

    const welcomeText =
      `👋 <b>স্বাগতম, ${firstName}!</b>\n\n` +
      `আমাদের গ্রুপে আপনাকে আন্তরিকভাবে স্বাগত জানাই। 🎉\n\n` +
      `🎬 <b>মুভি ও নাটক খুঁজছেন?</b>\n` +
      `আমাদের বট ${MOVIE_BOT} -এ সকল মুভি ও নাটক পাওয়া যায়!\n` +
      `শুধু বটে গিয়ে <b>সঠিক ইংরেজি নাম</b> লিখে পাঠান — তাহলেই পেয়ে যাবেন। ✅\n\n` +
      `⚠️ <i>গ্রুপে কোনো লিংক শেয়ার করবেন না।</i>\n\n` +
      `<i>(এই মেসেজটি ৩০ সেকেন্ড পর মুছে যাবে)</i>`;

    await sendAndScheduleDelete(chatId, welcomeText);
  }

  try {
    await bot.deleteMessage(chatId, msg.message_id);
  } catch {
  }
});

const LINK_REGEX = /(https?:\/\/|t\.me\/|www\.)[^\s]+/i;

bot.on("message", async (msg) => {
  if (!msg.text && !msg.caption) return;
  if (msg.chat.type === "private") return;

  const chatId = msg.chat.id;
  const userId = msg.from?.id;
  const messageId = msg.message_id;

  if (!userId) return;

  const text = msg.text ?? msg.caption ?? "";

  if (!LINK_REGEX.test(text)) return;

  try {
    const member = await bot.getChatMember(chatId, userId);
    if (["administrator", "creator"].includes(member.status)) return;
  } catch {
  }

  try {
    await bot.deleteMessage(chatId, messageId);
  } catch {
  }

  if (mutedUsers.has(userId)) return;

  const currentWarnings = (warnCount.get(userId) ?? 0) + 1;
  warnCount.set(userId, currentWarnings);

  const firstName = msg.from?.first_name ?? "ব্যবহারকারী";

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
      `একাধিকবার লিংক শেয়ার করার কারণে আপনার মেসেজ পাঠানোর অনুমতি বাতিল করা হয়েছে।\n\n` +
      `<i>(এই মেসেজটি ৩০ সেকেন্ড পর মুছে যাবে)</i>`;

    await sendAndScheduleDelete(chatId, muteText);
  }
});

bot.on("polling_error", (err) => {
  logger.error({ err }, "Telegram polling error");
});

logger.info("Telegram bot started");

export { bot };
