import json
import logging
import os
import random

from dotenv import load_dotenv
from google import genai
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is missing.")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing.")

logging.basicConfig(level=logging.INFO)

gemini = genai.Client(api_key=GEMINI_API_KEY)

MODEL = "gemini-3.8-flash"


FALLBACK_ROUNDS = [
    {
        "situation": "You open your fridge at 2 AM. A tiny penguin is sitting inside and looking at you.",
        "options": [
            "Ask what it's doing there",
            "Give it some cheese",
            "Close the fridge slowly",
            "Take a selfie",
        ],
        "ending": "The penguin says, 'Finally! Someone who understands midnight snacks.' It takes the cheese and disappears into the vegetable drawer.",
    },
    {
        "situation": "You press the elevator button. The elevator opens, but there is a chicken wearing sunglasses inside.",
        "options": [
            "Get inside",
            "Ask for directions",
            "Give the chicken a high-five",
            "Walk away",
        ],
        "ending": "The chicken looks at you and says, 'Wrong floor.' The elevator closes and goes up anyway.",
    },
    {
        "situation": "Your phone suddenly receives a message from YOURSELF saying: 'Don't eat the last biscuit.'",
        "options": [
            "Eat it immediately",
            "Listen to yourself",
            "Ask why",
            "Hide the biscuit",
        ],
        "ending": "You listen. Five seconds later, your future self sends another message: 'Good. That biscuit was mine.'",
    },
    {
        "situation": "A stranger walks up and says, 'Congratulations! You won absolutely nothing.'",
        "options": [
            "Celebrate anyway",
            "Ask for a refund",
            "Say thank you",
            "Run away",
        ],
        "ending": "You celebrate. Everyone nearby joins in. Somehow, you become the most successful person at winning nothing.",
    },
]


def option_keyboard(options):
    buttons = []

    for i, option in enumerate(options):
        buttons.append([
            InlineKeyboardButton(
                option,
                callback_data=f"choice:{i}"
            )
        ])

    return InlineKeyboardMarkup(buttons)


def again_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎲 AGAIN",
                callback_data="again"
            )
        ]
    ])


async def generate_round():

    prompt = """
Create ONE funny and harmless situation for a Telegram game called
WHAT HAPPENS?

The player sees a strange situation and chooses one of exactly four actions.

Return ONLY valid JSON:

{
  "situation": "short funny situation",
  "options": [
    "short option 1",
    "short option 2",
    "short option 3",
    "short option 4"
  ],
  "ending": "funny surprising result"
}

Rules:
- Exactly 4 options.
- Keep everything short.
- Make it funny, strange or surprising.
- Family friendly.
- No politics.
- No sexual content.
- No self-harm.
- No dangerous instructions.
"""

    try:
        response = gemini.models.generate_content(
            model=MODEL,
            contents=prompt,
        )

        text = response.text.strip()

        if text.startswith("```"):
            text = text.replace("```json", "")
            text = text.replace("```", "")
            text = text.strip()

        data = json.loads(text)

        if (
            not isinstance(data, dict)
            or not isinstance(data.get("situation"), str)
            or not isinstance(data.get("options"), list)
            or len(data["options"]) != 4
            or not isinstance(data.get("ending"), str)
        ):
            raise ValueError("Invalid response")

        return data

    except Exception:
        logging.exception("Gemini failed")
        return random.choice(FALLBACK_ROUNDS)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎲 START GAME",
                callback_data="again"
            )
        ]
    ])

    await update.message.reply_text(
        "🎭 WHAT HAPPENS?\n\n"
        "Something weird is about to happen.\n"
        "You choose what to do.\n"
        "Then we find out what happens.\n\n"
        "No scores. No leaderboard. Just play. 😄",
        reply_markup=keyboard
    )


async def new_game(query):

    game = await generate_round()

    query.message.chat_data["game"] = game

    text = (
        "🎭 WHAT HAPPENS?\n\n"
        f"{game['situation']}\n\n"
        "👇 What do you do?"
    )

    await query.edit_message_text(
        text=text,
        reply_markup=option_keyboard(game["options"])
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await query.answer()

    if query.data == "again":
        await new_game(query)
        return

    if not query.data.startswith("choice:"):
        return

    game = query.message.chat_data.get("game")

    if not game:
        await new_game(query)
        return

    try:
        choice_index = int(query.data.split(":")[1])
        choice = game["options"][choice_index]

    except (ValueError, IndexError):
        await query.edit_message_text(
            "Something went wrong 😅",
            reply_markup=again_keyboard()
        )
        return

    text = (
        "🎭 WHAT HAPPENS?\n\n"
        f"👉 You chose:\n{choice}\n\n"
        f"💥 WHAT HAPPENS:\n{game['ending']}"
    )

    await query.edit_message_text(
        text=text,
        reply_markup=again_keyboard()
    )


def main():

    app = Application.builder().token(
        TELEGRAM_BOT_TOKEN
    ).build()

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CallbackQueryHandler(button_handler)
    )

    print("YourOwnVision is running...")

    app.run_polling()


if __name__ == "__main__":
    main()
