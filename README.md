# НейроФинансы — Telegram bot (@neurogoals_bot)

Sales/enrollment bot for Zoe Zaraiskaya's 4-day program «НейроФинансы».
Python 3.11+, [aiogram 3](https://docs.aiogram.dev), SQLite.

## Run

```bash
cp .env.example .env        # fill in BOT_TOKEN and ADMIN_CHAT_ID
pip install -r requirements.txt
python -m neurofinance
```

or with Docker:

```bash
docker build -t neurofinance .
docker run -d --restart=always --env-file .env -v neurofinance-data:/data neurofinance
```

Only one process may poll a bot token at a time — stop the old bot before starting this one.

### Setting up the manager chat

Telegram bots cannot message a user by `@username`; they need a numeric chat id.

1. The manager (@ZoeRai) opens the bot and sends `/myid` (or adds the bot to a
   private team group and sends `/myid` there).
2. Put that number into `ADMIN_CHAT_ID` and the manager's personal id into `ADMIN_IDS`.

Everything for the team arrives in that chat: questions, «связаться с менеджером»
requests and payment receipts. **Reply** to any of those messages and the reply is
delivered to the user.

## Storyline

All texts live in [`neurofinance/texts.py`](neurofinance/texts.py) (steps 1–10, branches,
FAQ, reminder, closing message, payment details). Media files go into `media/`
(see [`media/README.md`](media/README.md)); missing media is skipped.

```
/start → Step 1 → (Подойдёт ли мне) Step 2 → branch → Step 3 → Step 4 → Step 5 (program)
                 → (Программа) Step 5           Step 5 → Это мне подходит → Step 6 (Zoe) → Step 7
                 → (Выбрать группу) groups      Step 5 → Узнать о формате → Step 7
                                                Step 5 → Пока сомневаюсь → doubt → format / FAQ / question
Step 7 → group → Step 8 (€ / ₽) → Step 9 (checklist) → payment methods → method details
       → «Я оплатил(а)» → receipt → manager confirms → Step 10 (group link)
```

Payment methods: € — PayPal, UBS, TWINT, Revolut, crypto; ₽ — Т-Банк, crypto.
Every method screen also has «Связаться с менеджером», which notifies the manager chat
and gives the user a link to @ZoeRai.

The persistent menu is a reply keyboard (plus the same items as `/commands`
in Telegram's Menu button).

## Automatic messages

- **Reminder**: sent once, `REMINDER_DELAY_HOURS` (default 20 h) after a user picks a
  group, if they haven't sent a payment receipt.
- **Closing message**: sent manually by an admin with `/closing` (preview + confirm),
  to everyone who hasn't paid.

## Admin commands

| Command | |
|---|---|
| `/stats` | users by status and group |
| `/closing` | broadcast «запись заканчивается» to all unpaid users |
| `/paid <user_id>` | confirm a payment made outside the bot and send step 10 |
| `/myid` | show the current chat id |
