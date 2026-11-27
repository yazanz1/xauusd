# מפרט: בחירת אסטרטגיה בבוט מתוך `.env` (`STRATEGY=wick` / `STRATEGY=breakout`)

**תאריך:** 22.11.2026
**סטטוס:** דרישה למימוש (Yazan / Cursor)
**מטרה:** שינוי של שורה אחת ב־`.env` יחליף את כל האסטרטגיה: הטבלה, ה־MAGIC, הנעילה, הפילטרים והסיכומים היומיים. בלי לגעת בקוד.

---

## 1. המצב היום

| מה | איך זה עובד היום |
|---|---|
| טבלה | `TABLE = "wick_strategy"` קבוע ב־`bot.py`, וגם `TRADES_TABLE` ב־`statics.py` |
| MAGIC | `260860` קבוע ב־`bot.py` |
| נעילה | `lock.py` עם הנעילה של wick בלבד (40/30, ‏M1). קוד הנעילה של הפריצה הוחלף |
| פילטרים | `STRATEGY` ב־`.env` כבר בוחר בין שרשרת ריקה לשרשרת הישנה |

כדי לחזור לפריצה צריך היום לשנות קוד.

## 2. שני הפרופילים

| שדה | `wick` (‏+60, ברירת מחדל) | `breakout` (פריצות, הגרסה הקודמת) |
|---|---|---|
| טבלת איתותים | `wick_strategy` | `gold_trades` |
| MAGIC | `260860` | `260831` |
| התראה ב־TradingView | `weak-strategy-webhook` → `wick_strategy` | ההתראה הישנה → `gold_trades` |
| נעילה | **40/30 ב־M1**: נגיעה ב־40% בנר דקה מ־`entry_time+5m` → SL ל־30% **באותו poll**. אם המחיר כבר מעבר ל־30%, סגירה בשוק כ־lock | **40/35 ב־M5**: נגיעה ב־40% בנר M5 סגור → `pend` → בתחילת הנר הבא SL ל־35% (`none → pend → locked`), כולל תיקונים 10.1 ו־10.2 מ־`entry_filters_A_B_C_D.md` |
| עמודות נעילה | `bot_lock_state`, ‏`bot_lock_sl`, ‏`bot_lock_pend_bar_time` | `lock_state`, ‏`lock_sl`, ‏`lock_pend_bar_time` |
| פילטרים בבוט | אין (`[]`). כל הסינון ב־Pine v3 (F1+F3+F4+F5) | השרשרת הקיימת: `israel_hours`, ‏`london_after_ny`, ‏`us_holiday_next_day`, ‏`ny_open`, ‏`ny_midday`, ‏`min_rr`, ‏`one_per_bar`, ‏`h1_ema_side` עם ה־MODE שלהם מ־env |
| PINE_EXIT_SYNC | פעיל | לפי מה שהיה בגרסת הפריצה |
| סיכום יומי (`gold_statics`) | מהטבלה `wick_strategy` | מהטבלה `gold_trades` |

## 3. דרישות

### R1. מודול פרופיל אחד: `strategy_profile.py`
```python
@dataclass(frozen=True)
class StrategyProfile:
    name: str                 # "wick" / "breakout"
    table: str                # wick_strategy / gold_trades
    magic: int                # 260860 / 260831
    lock_module: str          # "lock_wick" / "lock_breakout"
    lock_cols: dict           # state/sl/pend → שמות העמודות של הפרופיל
    pine_exit_sync: bool

PROFILES = {"wick": ..., "breakout": ...}

def load_profile() -> StrategyProfile:
    raw = (os.getenv("STRATEGY") or "wick").strip().lower()   # ריק → wick
    if raw not in PROFILES:
        raise SystemExit(f"Unknown STRATEGY={raw!r} — expected wick/breakout")
    return PROFILES[raw]
```
- ערך ריק או חסר נותן `wick`.
- ערך לא מוכר עוצר את הבוט עם שגיאה, ולא נופל בשקט לברירת מחדל.
- הפרופיל נטען **פעם אחת בעלייה**. אין החלפה תוך כדי ריצה.

### R2. להסיר קבועים מהקוד
- `bot.py`: ‏`TABLE` ו־`MAGIC` יבואו מ־`PROFILE.table` ומ־`PROFILE.magic`.
- `statics.py`: ‏`TRADES_TABLE` יבוא מ־`PROFILE.table`.
- `resync_week.py` ו־`test_open_trade.py`: אותו דבר, מהפרופיל.
- בכל הקוד לא תישאר מחרוזת `"wick_strategy"` או `"gold_trades"`, ולא מספר `260860` או `260831`, מחוץ ל־`strategy_profile.py`.

### R3. שתי נעילות נפרדות
- `lock.py` הנוכחי מועבר כמו שהוא ל־`lock_wick.py`.
- `lock_breakout.py` משוחזר מה־git, מהגרסה **שלפני ה־cutover ל־wick**: נעילת 40/35 על M5 עם `pend` והתיקונים 10.1 ו־10.2.
- **חובה:** גם ב־`lock_breakout.py`, כל השוואה של `bar["time"]` מול `entry_time` צריכה לעבור דרך `server_utc_offset`, כמו בתיקון של M1 (תיקון 10.1).
- `bot.py` קורא ל־`manage_lock` ול־`resolve_exit_reason` של המודול שבפרופיל, למשל עם `importlib.import_module(PROFILE.lock_module)`.
- `LOCK_SL_FRAC` מה־env חל רק על wick. לפריצה יש `LOCK_SL_FRAC_BREAKOUT`, ברירת מחדל 0.35. כך משתנה של אסטרטגיה אחת לא משפיע על השנייה.

### R4. פילטרים לפי הפרופיל
`active_filters()` מחזיר `[]` אם `PROFILE.name == "wick"`, ואת השרשרת הישנה אם `PROFILE.name == "breakout"`. הבדיקה היא מול הפרופיל, לא מול `os.getenv` ישירות.

### R5. מעבר בטוח בין אסטרטגיות
כל בוט מנהל רק פוזיציות עם ה־MAGIC שלו ושורות מהטבלה שלו. מעבר בזמן שיש עסקאות פתוחות יעזוב אותן בלי נעילה ובלי סנכרון. לכן:
- **בעלייה:** אם יש ב־MT5 פוזיציות פתוחות עם ה־MAGIC **של הפרופיל השני**, לכתוב בלוג ברמת WARNING:
  `Open positions of other strategy (magic=…) — not managed by this run`
- אם `STRICT_SWITCH=1` (ברירת מחדל), לא לעלות בכלל ולהודיע: "סגור את העסקאות של האסטרטגיה השנייה או חזור ל־STRATEGY הקודם".
- `STRICT_SWITCH=0` מאפשר לעלות בכל זאת, למשל אם הפוזיציות נוהלו ידנית.

### R6. סיכום יומי בלי ערבוב
- להוסיף ל־`gold_statics` עמודה `strategy text not null default 'breakout'`, כי השורות הקיימות הן של הפריצה. הסיכום היומי כותב `strategy = PROFILE.name`.
- **מיגרציה** (אתה מריץ):
  ```sql
  alter table public.gold_statics add column if not exists strategy text not null default 'breakout';
  ```
  אם יש מפתח ייחודי על התאריך, להרחיב אותו ל־`(date_idt, strategy)`.

### R7. לוג עלייה
שורה אחת:
```
STRATEGY=wick table=wick_strategy magic=260860 lock=lock_wick(40/30 M1) filters=[] pine_exit_sync=on
```

### R8. `.env.example` ותיעוד
- `.env.example`: ‏`STRATEGY=wick` עם הערה על שני הערכים האפשריים ומה כל אחד מחליף.
- `STRATEGY.md` / `MEMORY.md`: פסקה "שני פרופילים" עם הטבלה מסעיף 2.
- `VPS_UPDATES.md`: רשומה על השינוי.

### R9. מה לא משתנה
- `gann.py` (רמות גאן) רץ אותו דבר בשני הפרופילים.
- `mt5_client.py`, ‏`sessions/`, ‏`watchdog` לא משתנים.
- **TradingView:** ה־`.env` בוחר רק מה הבוט קורא. את ההתראה המתאימה (wick או פריצה) צריך להפעיל ידנית. אם שתי ההתראות פעילות, כל אחת ממלאת את הטבלה שלה, והבוט מבצע רק את הטבלה של הפרופיל.

## 4. בדיקות קבלה (ב־DRY_RUN=1 ואז בדמו)

| # | בדיקה | צפוי |
|---|---|---|
| T1 | `STRATEGY=wick` | לוג R7, קורא רק `wick_strategy`, ‏MAGIC ‏260860, ‏`filters=[]`, נעילה M1 עם שורת `M1 lock scan` |
| T2 | `STRATEGY=` ריק או חסר | זהה ל־T1 |
| T3 | `STRATEGY=breakout` | קורא `gold_trades`, ‏MAGIC ‏260831, שרשרת 8 פילטרים, נעילה M5 עם `LockPend` ואז `Locked` בנר הבא, כותב ל־`lock_*` ולא ל־`bot_lock_*` |
| T4 | `STRATEGY=xyz` | הבוט לא עולה, הודעת שגיאה ברורה |
| T5 | wick עם פוזיציה פתוחה, מעבר ל־breakout, ‏`STRICT_SWITCH=1` | לא עולה, הודעה על פוזיציה של magic 260860 |
| T6 | כמו T5 עם `STRICT_SWITCH=0` | עולה עם WARNING, ולא נוגע בפוזיציה של 260860 |
| T7 | סיכום יומי בכל פרופיל | שורה ב־`gold_statics` עם `strategy` הנכון, בלי ערבוב |
| T8 | חיפוש בקוד | המחרוזות והמספרים של הטבלאות וה־MAGIC מופיעים רק ב־`strategy_profile.py` |

## 5. אופציה לעתיד (לא לממש עכשיו)
להריץ את שתי האסטרטגיות **במקביל**: שני תהליכים, כל אחד עם `.env` משלו, ‏`heartbeat` נפרד ו־watchdog נפרד. בזכות ה־MAGIC והטבלאות הנפרדים הם לא יתנגשו. זה דורש את R1–R6 כבסיס, ובנוסף קבצי heartbeat, לוג ו־offset לפי פרופיל (`heartbeat_wick.txt`, ‏`server_utc_offset.txt` אפשר משותף).
