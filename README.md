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
   | `ADMIN_PASSWORD` | password for the admin panel at `/admin` |

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

## Рабочий бот (Trello)

A second bot for the team's work group: commands in Telegram create and manage cards on a
Trello board. Code: [`workbot/`](workbot). It is served by the same Vercel deployment
(`/api/workbot`) and stores its data in the same database (`work_*` tables).

**Setup (once):**

1. **Bot.** @BotFather → `/newbot` → token into `WORK_BOT_TOKEN`. Leave *Group Privacy* on:
   the bot then sees only commands and replies to its own messages, not the whole chat.
2. **Trello account for the bot.** Best a separate account (e.g. «NeuroGoals Bot»): the board,
   comments and changes made through the bot appear under it.
   - Log in as that account, open <https://trello.com/power-ups/admin> → *New* → create a Power-Up
     (any name, any workspace) → *API key* → *Generate a new API key* → `TRELLO_KEY`.
   - On the same page click the *Token* link, allow access → `TRELLO_TOKEN`.
     (Or open `https://trello.com/1/authorize?expiration=never&scope=read,write&response_type=token&key=<TRELLO_KEY>&name=NeuroGoals%20Bot`.)
3. **Vercel env:** `WORK_BOT_TOKEN`, `WORK_WEBHOOK_SECRET` (random string), `TRELLO_KEY`,
   `TRELLO_TOKEN`; optional `WORK_TZ` (default Europe/Moscow). Redeploy.
4. **Webhook:** open `https://<your-domain>/api/workbot/setup?key=<WORK_WEBHOOK_SECRET>` →
   `"ok": true`, and `trello_account` shows the bot's Trello login.
5. **Group:** add the bot to the work group, send `/chatid` there → put the number into
   `WORK_CHAT_ID` → redeploy. Until then the bot ignores the group.
6. **Board:** a group admin sends `/setup`. The bot creates the board «NeuroGoals · Работа»:
   lists Входящие → К выполнению → В работе → На проверке → Готово, plus События and
   Заметки и требования; type labels (Задача, Событие, Требование, Заметка, Баг, Идея) and
   priority labels.
7. **People:** everyone sends `/link <their Trello username>` in the group: the bot adds them to
   the board and can assign them by @username. No Trello account yet: `/link name@mail.com`
   sends an invitation. Free Trello workspaces allow up to 10 collaborators.

**Commands** (the bot answers in Russian; `/help` in the group shows the same):

| Command | |
|---|---|
| `/task Обновить лендинг @anna !срочно до пятницы #маркетинг` | task; `@user` assigns (`@я` = me), `!срочно/!средний/!низкий`, `#tag` label, due date |
| `/event Вебинар 12.10 19:00` · `/req` · `/note` · `/idea` · `/bug` | other card types; lines `- …` below become a checklist, other lines the description |
| reply `/task` (or `/note`…) to any message | card made from that message |
| `/done 42` · `/move 42 на проверке` · `/assign 42 @ivan` · `/due 42 завтра` | change a card; instead of the number you can reply to the bot's card message |
| `/edit 42 …` · `/comment 42 …` · `/card 42` · `/delete 42` · `/restore 42` | `/delete` archives; deleting for good is admin-only, with confirmation |
| reply with plain text to a card message | becomes a Trello comment |
| `/list` · `/list мои` · `/list @anna` · `/list просрочено` · `/list неделя` · `/list все` · `/mytasks` · `/overdue` | lists |
| `/link` · `/unlink` · `/members` · `/board` · `/setup` · `/chatid` | setup |

Card messages have buttons: ✅ Готово, 🔄 Статус, 🙋 Беру, 👥 Назначить, ⏰ Срок, 🗄 В архив.
Dates understood: `сегодня, завтра, послезавтра, в пятницу / пт, 12.10, 12.10.2027, 12.10 18:00,
в 15:00, через 3 дня / 2 часа / неделю`. Without a time: 18:00 (events 10:00).

Locally: `python -m workbot` (long polling, SQLite unless `DATABASE_URL` is set; use a separate
test bot). Tests: `pip install pytest && python -m pytest tests`.

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

## Admin panel: editing messages

`https://<your-domain>/admin` (password: `ADMIN_PASSWORD`) lists every message the bot sends,
grouped by step. Each one opens in a visual editor (bold, italic, underline, strikethrough,
links, quotes, code) and can get a picture. Changes apply to the bot immediately.

- **Variables** such as `{group}`, `{dates}`, `{amount}` are filled in when the message is sent;
  the editor shows the ones each message supports as buttons.
- **Pictures** are uploaded to Telegram (sent to the admin chat and deleted at once), so no
  file storage is needed. A text over 1024 characters is sent as a separate message under the picture.
- **«Сохранить и прислать мне»** sends a preview to `ADMIN_CHAT_ID`.
- **«Сбросить»** restores the original text and removes the picture.
- FAQ answers that are empty are hidden in the bot; filling one in makes the question appear.

Default texts live in [`neurofinance/texts.py`](neurofinance/texts.py); the catalogue of editable
messages is [`neurofinance/content.py`](neurofinance/content.py). Button labels are not editable yet.

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
