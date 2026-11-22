# VPS Updates — xaubot

יומן העלאות ל־Windows VPS. בכל `git push` / `git pull` על ה־VPS — להוסיף ערך **בראש** הקובץ (מתחת לכותרת זו).

תזכורת אוטומטית: `.cursor/hooks.json` + `.cursor/hooks/vps_update_reminder.py` — לפני/אחרי `git push` ב־Cursor מזכירים לעדכן את הקובץ.

פורמט ערך חדש:

```text
## YYYY-MM-DD — כותרת קצרה
- Commit: `<hash>` (אם יש)
- מה עלה / מה השתנה (מפורט)
- הערות הפעלה ב־VPS (pull, restart watchdog, וכו')
```

סימון: `⭐ חשוב להשוואה` = שינוי שישמש להשוואת ביצועים / נעילות / יומן מול תקופות קודמות.

---

## ⭐ חשוב להשוואה — 2026-11-22 — Cutover ל־wick_strategy (+60)
- Commit: _(יוזן אחרי commit)_
- **פיתוח היום (בקצרה):** מעבר מפריצה ל־+60; נעילת 40/30 על M1 עם תיקון שעון־שרת; פילטרי בוט כבויים תמיד; סליפג׳ יחסי לגודל הסטופ. גרסת הפריצה נשארת ב־git לפני הקומיט.
- `TABLE=wick_strategy`, `MAGIC=260860` (נפרד מפריצה 260831)
- `active_filters()` תמיד `[]` — לא קורא `STRATEGY` מה־`.env`; מודולי `bot1_filters/` נשארים ולא נטענים
- עמודות `bot_lock_*` (SQL `wick_bot_lock_columns`); הוובהוק ממשיך לכתוב `lock_state`/`stop` ליומן בלבד
- נעילה **40/30**: זיהוי על M1 מ־`entry_time+5m`; `set_sl` באותו poll; אם המחיר כבר עבר את ה־30% → **סגירה בשוק** כ־lock
- `PINE_EXIT_SYNC`: יומן `closed` + פוזיציה פתוחה >10ש׳ → סגירה בשוק
- סליפג׳: `min(MAX_SLIP_USD=1.5, MAX_SLIP_STOP_FRAC=0.20 × |entry−stop|)`
- לוג עלייה: `wick table=wick_strategy magic=260860 filters=[]`
- `MAX_OPEN=6`, `LOCK_SL_FRAC=0.30`
- `statics.py` קורא מ־`wick_strategy` ל־`gold_statics`
- **תיקון M1:** `m1_bars_since` / `m1_open_at` ממירים UTC↔שעון שרת; ל־`copy_rates_range` נשלח datetime עם `tzinfo=UTC` (לא naive). לוג: `scan_from_utc` ≈ `first_bar_utc`
- **VPS:** לפני live — `DRY_RUN=1` ולוודא בלוג מציאת שורות + `mt5_error` עם `DRY_RUN would …`; לבדוק שאין פוזיציה פתוחה עם MAGIC 260831; אחרי pull להפעיל מחדש watchdog/bot

## 2026-09-21 — הפרדת יומן Pine משדות ביצוע MT5
- Commit: `493f4d6`
- מעקב עסקאות מועתקות: `mt5_ticket` + `mt5_closed_at` null + `date_idt` בתוך 7 ימים — **לא** לפי `status`
- בסגירת MT5 נכתבים רק: `mt5_close_price`, `mt5_closed_at` (UTC), `mt5_profit`, `mt5_exit_reason`
- הבוט **לא** נוגע ב־`status` / `exit` / `exit_reason` / `pips` / `usd_0_3` (Pine)
- נעילה: דילוג לפני clamp אם lock SL בצד הלא נכון של המחיר; clamp לא חוצה `mt5_fill_price`
- `deal.time` (שעון ברוקר) → UTC עם offset שעתי מ־`tick.time`, עם cache כשהטיק ישן
- Cutover: לפני 21/9 שורות עם `mt5_ticket` — שדות היומן עלולים להיות דרוסים ע״י MT5; אחרי התאריך — יומן = Pine, ברוקר = `mt5_*`

## 2026-09-20 — Watchdog: נתיב מלא + לוגים
- Commit: `c44b778`
- `$EntryScript` = נתיב מלא ל־`bot.py` (זיהוי תהליך)
- `Start-Bot` מפנה stdout/stderr ל־`bot_out.log` / `bot_err.log`
- `watchdog.local.ps1` רק למשתנים מקומיים (`$Python`, `$StaleSec`)

## 2026-09-19 — כלי resync + חיזוק נעילה + תיקון סנכרון סגירות
- Commits: `26a9945`, `8321a43`, `b2ed3af`
- `resync_week.py`: dry-run ברירת מחדל; `--apply` רק אחרי בדיקה — מחשב מחדש closes מ־MT5 deals
- נעילה / `set_sl`: רצפת `min_stop_distance×1.2`, דילוג בצד הלא נכון, `normalize_price`, לוג `sent_sl`+`retcode`, הדפסת `stops_level` בהפעלה
- סנכרון סגירות: `history_deals_get(position=ticket)` אחרי priming טווח; בלי התאמת `order==ticket`; אין סימון closed בלי OUT; ORPHAN בלוג; DEAL_REASON מתוקן (4=sl, 5=tp); `resolve_exit_reason` לפי מחיר מול stop/tp/lock

## 2026-09-13 — Heartbeat / watchdog / סטטיסטיקה / DRY_RUN חי
- Commits: `3be0e39`, `f3687ae`, `a37ad20`, `ef30886` (ואגריגציות מאותו יום)
- אחרי כל `run_cycle` נכתב `heartbeat.txt`; `watchdog.ps1` מפעיל מחדש אם heartbeat ישן
- חיזוק נתיב Python ל־watchdog; gitignore ל־`heartbeat.txt` / לוגים / `*.bak`
- `statics.py` → צבירה יומית ל־`gold_statics` (tp/sl/lock + רווח)
- פחות PATCH לסופבייס (רק אם profit/SL/TP השתנו); cache ל־Gann/statics אחרי שמירה
- ברירת מחדל `DRY_RUN=0` (פתיחות חיות)
- `israel_hours`: חלון צהריים עד `14:30` (במקום 14:00)

## ⭐ חשוב להשוואה — 2026-09-12 — נעילה 50/30 → 40/35
- זה השינוי שסומן במפורש להשוואת תקופות (לפני/אחרי).
- **לפני:** נגיעה ב־**50%** מ־`entry→tp` על נר M5 סגור → בנר הבא SL ל־**30%** (`none → pend → locked`)
- **אחרי (נוכחי):** נגיעה ב־**40%** → בנר הבא SL ל־**35%**
- נוסחה נשארת מ־`entry`+`tp` של הוובהוק (לא `mt5_fill_price`)
- קבצים: בעיקר `lock.py` (`LOCK_HIT_FRAC=0.4`, `LOCK_SL_FRAC=0.35`) + תיעוד STRATEGY/MEMORY/rule
- **איך להשוות אחר כך:** עסקאות/ימים לפני החלפה = התנהגות 50/30; אחריה = 40/35. אל תערבב מדדי lock/`usd_0_3` בין התקופות בלי לציין איזו נעילה הייתה פעילה.

## 2026-09-12 — פילטרים + נעילה מקומית ראשונה (50/30)
- נעילת **50/30** ראשונה בבוט (לפני ההחלפה ל־40/35 באותו יום / בסמוך)
- עמודות `lock_state` / `lock_sl` / `lock_pend_bar_time` / `orig_stop`
- פילטרים חדשים תחת `filters/`:
  - `israel_hours` — אין כניסות `[12:00,14:00)` ו־`[22:00,24:00)` IDT (עודכן אחר כך עד 14:30)
  - `london_after_ny` — מ־15:30 IDT חוסמים `color=london`
  - `us_holiday_next_day` — יום אחרי חג US, כניסות רק עד 10:00 IDT
  - `ny_open` — אין כניסות `[15:20,15:30)` IDT

## 2026-09-08 — העלאה ראשונה / בסיס VPS
- Commits: `cb72190`, merge `4251b76`, initial `12d0611`
- בוט העתקה: `gold_trades` → MT5 (market BUY/SELL)
- Poll + הגנות: `MAX_AGE_SEC`, `MAX_SLIP_USD`, `MAX_OPEN`, dry-run, claim על `copied_at`
- עמודות `mt5_*` למילוי/סגירה בלי לדרוס `entry` של היומן
- סשני MT5 ב־`sessions/` + reconnect
- (רקע מוקדם יותר בריפו/תיעוד: Gann, חיבור `.env` — לא בהכרח גל VPS נפרד מתועד)
