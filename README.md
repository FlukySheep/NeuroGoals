# НейроФинансы — Telegram bot (@neurogoals_bot)

Sales/enrollment bot for Zoe Zaraiskaya's 4-day program «НейроФинансы».
Python 3.11+, [aiogram 3](https://docs.aiogram.dev), Postgres on Vercel (SQLite locally).

## Deploy on Vercel

The bot runs as a webhook: Telegram calls `https://<your-domain>/api/telegram`.
Data lives in Postgres (Neon). An hourly Vercel Cron sends the reminders.

1. **Import the project.** Vercel → *Add New… → Project* → import `FlukySheep/NeuroGoals`.
   Framework preset: *Other*; leave build settings empty. Deploy.
2. **Add the database.** Project → *Storage* → *Create Database* → **Neon** (Postgres)
   → connect it to the project. This sets `DATABASE_URL` automatically.
3. **Add environment variables** (*Settings → Environment Variables*):

   | Name | Value |
   |---|---|
   | `BOT_TOKEN` | token from @BotFather |
   | `WEBHOOK_SECRET` | random letters/digits, 32+ chars |
   | `CRON_SECRET` | another random string |
   | `ADMIN_CHAT_ID` | manager's chat id (step 6 below) |
   | `GROUP_LINK_WEEKEND`, `GROUP_LINK_WEEKDAY` | invite links to the participant chats |

   Optional: `ADMIN_IDS`, `MANAGER_USERNAME`, `REMINDER_DELAY_HOURS`, `PUBLIC_URL` (see `.env.example`).
4. **Redeploy** (*Deployments → ⋯ → Redeploy*). Env changes only apply after a redeploy.
5. **Connect Telegram.** Open `https://<your-domain>/api/setup?key=<WEBHOOK_SECRET>` in a browser.
   It should answer `"ok": true` with the webhook URL. This replaces any previous webhook
   on the bot. Re-open it whenever the domain changes.
6. **Connect the manager.** The manager sends `/myid` to the bot → put the number into
   `ADMIN_CHAT_ID` → redeploy.
7. **Media.** The manager sends the bot (in private): a photo captioned `портрет`,
   a photo captioned `программа`, and the video note (кружочек). `/media` shows what is set.

**Custom domain:** *Settings → Domains* → add e.g. `bot.neurogoals.club`, add the DNS record
Vercel shows, then repeat step 5 on the new domain.

**Hobby plan:** Vercel Hobby only allows daily cron jobs (and is for non-commercial use).
On Hobby, delete the `crons` block from `vercel.json` and call
`https://<your-domain>/api/cron?key=<CRON_SECRET>` hourly from an external scheduler
such as cron-job.org.

## Run locally / on a server (long polling)

```bash
cp .env.example .env        # fill in BOT_TOKEN and ADMIN_CHAT_ID
pip install -r requirements.txt
python -m neurofinance      # uses SQLite unless DATABASE_URL is set
```

Polling removes the webhook, so don't run it against the production bot while Vercel serves it
(use a separate test bot from @BotFather). Docker: `docker build -t neurofinance . &&
docker run -d --env-file .env -v neurofinance-data:/data neurofinance`.

Everything for the team arrives in the `ADMIN_CHAT_ID` chat: questions,
«связаться с менеджером» requests and payment receipts. **Reply** to any of those messages
and the reply is delivered to the user.

## Storyline

All texts live in [`neurofinance/texts.py`](neurofinance/texts.py) (steps 1–10, branches,
FAQ, reminder, closing message, payment details). Media files go into `media/`
or are uploaded by the manager directly in the bot (see step 7 above); missing media is skipped.

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

The menu (что такое, программа, формат, стоимость, обо мне, FAQ, записаться, вопрос) is
Telegram's **Menu** button next to the input field: hidden until tapped.

## Automatic messages

- **Reminder**: sent once, `REMINDER_DELAY_HOURS` (default 20 h, checked hourly) after a
  user picks a group, if they haven't sent a payment receipt.
- **Closing message**: sent manually by an admin with `/closing` (preview + confirm),
  to everyone who hasn't paid. Each user gets it at most once.

## Admin commands

| Command | |
|---|---|
| `/stats` | users by status and group |
| `/closing` | broadcast «запись заканчивается» to all unpaid users |
| `/paid <user_id>` | confirm a payment made outside the bot and send step 10 |
| `/media` | which photos / video note are set, and how to replace them |
| `/myid` | show the current chat id |
