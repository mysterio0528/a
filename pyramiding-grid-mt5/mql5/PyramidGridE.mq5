//+------------------------------------------------------------------+
//| PyramidGridE.mq5                                                  |
//| ピラミッディンググリッド戦略 — ポリシーE（モード状態機械）実装     |
//|                                                                   |
//| 対応ドキュメント: pyramiding-grid-mt5/STRATEGY_DESIGN.md v0.2     |
//|                   pyramiding-grid-mt5/RESEARCH_NOTE.md            |
//| 対応シミュレータ: analysis/simulate.py policy "E"                 |
//|                                                                   |
//| アーキテクチャ:                                                   |
//|   ブリック検出は合成方式（mid閾値クロスで成行発注）。             |
//|   - ペンディング逆指値を置かないため、モード別の参加制御で        |
//|     注文の置き直し/削除が発生せず、業者の修正回数制限に触れない   |
//|   - 棄権(CHOP)が「注文を出さない」だけで実現でき、約定漏れがない  |
//|   - 検出オーバーシュート＝実効スリッページとして毎回計測・表示    |
//|   検証済みセマンティクス対応:                                     |
//|     NORMALの利確 = ブローカー側TP（エントリー時に設定、以後不変） |
//|     TRENDのトレイル = 逆方向ブリック確定で成行クローズ            |
//|     反転時の滓処理 = 新規を「消費」せず滓を直接クローズ           |
//|       （sim比で往復コスト1回分有利。RESEARCH_NOTE §4参照）        |
//|                                                                   |
//| 注意: 本コードは未コンパイル環境で記述。MetaEditorでのコンパイル  |
//|       → ストラテジーテスター → デモ口座の順で必ず検証すること。   |
//+------------------------------------------------------------------+
#property copyright "pyramiding-grid-mt5"
#property version   "0.20"
#property description "Mode-state-machine pyramiding grid (policy E)"

#include <Trade\Trade.mqh>

//=== 入力パラメータ ==================================================
input group "--- グリッド (E3/E4: 動的d・非対称反転) ---"
input double InpDbps           = 20.0;   // d: グリッド幅 [bps of price]
input double InpRevMult        = 2.0;    // 反転幅倍率 r (2.0=標準Renko化, 検証で9倍改善)
input bool   InpAutoDFloor     = true;   // ATRでdの下限を自動補正(チョップ床)
input double InpDFloorAtrMult  = 0.20;   // d下限 = ATR(H1,24)×この係数
input double InpRho            = 0.90;   // ρ = TP/d (NORMALの回転利確)

input group "--- モード状態機械 (6章) ---"
input int    InpTrailAfter     = 3;      // TREND判定: 同方向連続ブリック数
input int    InpChopAlt        = 3;      // CHOP判定: 交互(長さ1ラン)連鎖数
input int    InpGovWindow      = 30;     // E1ガバナー窓 [ブリック数] (ブリック時間=ボラ不変)
input int    InpGovBase        = 2;      // E1: 基礎許容エントリー数
input double InpGovAlpha       = 1.0;    // E1: 利確1回あたり追加許容数

input group "--- バスケット/予算 (5.2 #0,#1 / 4.4) ---"
input double InpThetaB         = 3.0;    // バスケットTP目標 [d単位]
input double InpBudgetD        = 20.0;   // 予算ストップ [d単位] (小さく死ぬ制度)
input bool   InpBasketTrail    = true;   // 盤面トレイリング(HWMから押しで全決済)
input double InpBasketKeep     = 0.67;   // トレイル: HWMのこの比率を割ったら全決済
input double InpMinKeepFrac    = 0.50;   // 全決済中断: 目標のこの比率を割ったら中断
input double InpEmgStopMult    = 1.5;    // tickレベル緊急ストップ = 予算×この係数

input group "--- ロット/リスク (5.4 / 4.2) ---"
input double InpLots           = 0.01;   // 基本ロット(均一)
input double InpCommPerLotSide = 0.0;    // 片道手数料 [口座通貨/1.0lot] (χ計測用)
input int    InpMaxGross       = 12;     // グロス上限 [ポジ数] (sim最大7+余裕)
input double InpMarginStage1   = 0.50;   // 証拠金使用率: ロット半減
input double InpMarginStage2   = 0.70;   // 証拠金使用率: 新規停止
input double InpMarginStage3   = 0.85;   // 証拠金使用率: 強制縮小

input group "--- ゲート (E5/E6/E8) ---"
input double InpSpreadGateMult = 3.0;    // スプレッド > 中央値×この係数で新規停止
input double InpTickRateMult   = 3.0;    // tick速度 > 平常×この係数で新規停止 (R6)
input int    InpSwapPauseDay   = 3;      // スワップ3倍日 (3=水曜, -1=無効)
input int    InpSwapPauseHour  = 22;     // 同日この時(サーバー時刻)以降は新規停止
input int    InpFriFlattenHour = -1;     // 金曜この時に全決済(-1=無効, XAU週末ギャップ対策)

input group "--- 実行系 (8章) ---"
input long   InpMagic          = 552210; // マジックナンバー
input int    InpSlippagePts    = 30;     // 許容スリッページ [points]
input int    InpMaxConsecErr   = 5;      // 連続発注エラーでHALT
input bool   InpVerboseLog     = true;   // 詳細ログ

//=== 定数・型 ========================================================
enum EMode   { MODE_NORMAL=0, MODE_TREND=1, MODE_CHOP=2 };
enum ERole   { ROLE_NORMAL=0, ROLE_TREND=1 };   // ポジションの役割
enum EHalt   { HALT_NONE=0, HALT_ERRORS=1, HALT_ACCOUNT=2, HALT_SYNC=3 };

#define STATE_VERSION 2
#define MAX_BRICKS_PER_TICK 200

//=== グローバル状態 ==================================================
CTrade  trade;

// --- ブリックエンジン (renko.py RenkoBuilder相当)
double  g_anchor      = 0.0;   // アンカー(理論グリッド価格, mid基準)
int     g_lastDir     = 0;     // 最後のブリック方向 (+1/-1/0)
double  g_d           = 0.0;   // 現在のグリッド幅 [price]
long    g_brickCount  = 0;     // 通算ブリック番号(ガバナー窓の時計)
int     g_runLen      = 0;     // 現在の同方向連続数
int     g_altChain    = 0;     // 交互連鎖数 (BSBS検出器)
EMode   g_mode        = MODE_NORMAL;

// --- E1ガバナー (ブリック番号のリングバッファ)
long    g_fillBricks[];        // 直近の新規約定ブリック番号
long    g_tpBricks[];          // 直近のTP決済ブリック番号

// --- 盤面会計 (4.4: 実現+ロック勘定, sim realized_since_reset相当)
double  g_realizedSinceReset = 0.0;  // リセット以降の実現損益 [口座通貨]
double  g_lockedSinceReset   = 0.0;  // うち滓ロック分 (参考表示)
double  g_basketHWM          = 0.0;  // 盤面合計のハイウォーターマーク
int     g_nBasketTP = 0, g_nBudgetStop = 0, g_nTrail = 0, g_nOrphan = 0, g_nTP = 0;

// --- 役割マップ (ticket -> role)。ブローカーにコメントを剥がされても耐える
ulong   g_roleTicket[];
int     g_roleValue[];

// --- 統計/ゲート
double  g_ovsSumPts = 0.0;     // オーバーシュート累計 [points] (=実効スリッページ計測)
long    g_ovsN      = 0;
double  g_spreadMed = 0.0;     // スプレッド中央値の指数近似
double  g_tickRate  = 0.0;     // tick/秒 EMA
datetime g_lastTickSec = 0;
int     g_ticksThisSec = 0;
long    g_gatedBricks = 0;     // 参加棄権したブリック数(表示用)
// 継続確率のオンライン推定 (6.3: 自己約定列センサー)
int     g_transSame = 0, g_transTotal = 0;

// --- 実行系
int     g_consecErr  = 0;
EHalt   g_halt       = HALT_NONE;
bool    g_closeByOK  = false;
int     g_atrHandle  = INVALID_HANDLE;
string  g_stateFile;
string  g_lockGV;

//=== ユーティリティ ==================================================
void VLog(const string s) { if(InpVerboseLog) Print("[PGRID] ", s); }

double Mid()
{
   return (SymbolInfoDouble(_Symbol, SYMBOL_BID) +
           SymbolInfoDouble(_Symbol, SYMBOL_ASK)) * 0.5;
}

double SpreadPts()
{
   return (SymbolInfoDouble(_Symbol, SYMBOL_ASK) -
           SymbolInfoDouble(_Symbol, SYMBOL_BID)) / _Point;
}

// 1ロットあたり 価格1単位の金額価値 (d単位→金額の換算に使用)
double MoneyPerPriceUnit(const double lots)
{
   double tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(ts <= 0.0) return 0.0;
   return lots * tv / ts;
}

double NormalizeLots(double lots)
{
   double mn = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double mx = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double st = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(st > 0) lots = MathFloor(lots / st + 1e-9) * st;
   return MathMin(MathMax(lots, mn), mx);
}

// 証拠金使用率 (0..1)
double MarginUsage()
{
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   if(eq <= 0) return 1.0;
   return AccountInfoDouble(ACCOUNT_MARGIN) / eq;
}

//=== 役割マップ ======================================================
void RoleSet(const ulong ticket, const int role)
{
   int n = ArraySize(g_roleTicket);
   for(int i = 0; i < n; i++)
      if(g_roleTicket[i] == ticket) { g_roleValue[i] = role; return; }
   ArrayResize(g_roleTicket, n + 1);
   ArrayResize(g_roleValue,  n + 1);
   g_roleTicket[n] = ticket;
   g_roleValue[n]  = role;
}

int RoleGet(const ulong ticket)   // 不明チケットはTP有無で推定(リシンク用)
{
   for(int i = 0; i < ArraySize(g_roleTicket); i++)
      if(g_roleTicket[i] == ticket) return g_roleValue[i];
   if(PositionSelectByTicket(ticket))
      return (PositionGetDouble(POSITION_TP) > 0.0) ? ROLE_NORMAL : ROLE_TREND;
   return ROLE_NORMAL;
}

void RoleDrop(const ulong ticket)
{
   int n = ArraySize(g_roleTicket);
   for(int i = 0; i < n; i++)
      if(g_roleTicket[i] == ticket)
      {
         g_roleTicket[i] = g_roleTicket[n-1];
         g_roleValue[i]  = g_roleValue[n-1];
         ArrayResize(g_roleTicket, n - 1);
         ArrayResize(g_roleValue,  n - 1);
         return;
      }
}

//=== 自ポジション走査 ================================================
bool IsOurs()
{
   return PositionGetString(POSITION_SYMBOL) == _Symbol &&
          PositionGetInteger(POSITION_MAGIC) == InpMagic;
}

int GrossCount()
{
   int c = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
      if(PositionGetTicket(i) > 0 && IsOurs()) c++;
   return c;
}

// 盤面のフローティング損益 [口座通貨] (swap込み)
double FloatingPnL()
{
   double s = 0.0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
      if(PositionGetTicket(i) > 0 && IsOurs())
         s += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
   return s;
}

double BasketPnL() { return g_realizedSinceReset + FloatingPnL(); }

//=== 状態永続化 (8章: 再起動リシンク) ================================
void StateSave()
{
   int h = FileOpen(g_stateFile, FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(h == INVALID_HANDLE) return;
   FileWriteString(h, StringFormat("%d\n%.10f\n%d\n%.10f\n%I64d\n%d\n%d\n%.2f\n%.2f\n%.2f\n",
      STATE_VERSION, g_anchor, g_lastDir, g_d, g_brickCount, g_runLen, g_altChain,
      g_realizedSinceReset, g_lockedSinceReset, g_basketHWM));
   for(int i = 0; i < ArraySize(g_roleTicket); i++)
      FileWriteString(h, StringFormat("R,%I64u,%d\n", g_roleTicket[i], g_roleValue[i]));
   FileClose(h);
}

bool StateLoad()
{
   if(!FileIsExist(g_stateFile)) return false;
   int h = FileOpen(g_stateFile, FILE_READ|FILE_TXT|FILE_ANSI);
   if(h == INVALID_HANDLE) return false;
   if((int)StringToInteger(FileReadString(h)) != STATE_VERSION) { FileClose(h); return false; }
   g_anchor             = StringToDouble(FileReadString(h));
   g_lastDir            = (int)StringToInteger(FileReadString(h));
   g_d                  = StringToDouble(FileReadString(h));
   g_brickCount         = StringToInteger(FileReadString(h));
   g_runLen             = (int)StringToInteger(FileReadString(h));
   g_altChain           = (int)StringToInteger(FileReadString(h));
   g_realizedSinceReset = StringToDouble(FileReadString(h));
   g_lockedSinceReset   = StringToDouble(FileReadString(h));
   g_basketHWM          = StringToDouble(FileReadString(h));
   while(!FileIsEnding(h))
   {
      string line = FileReadString(h);
      string parts[];
      if(StringSplit(line, ',', parts) == 3 && parts[0] == "R")
         RoleSet((ulong)StringToInteger(parts[1]), (int)StringToInteger(parts[2]));
   }
   FileClose(h);
   return true;
}

//=== グリッド幅の導出 (E3: 動的d + 床) ===============================
double ComputeD(const double refPrice)
{
   double d = refPrice * InpDbps * 1e-4;
   if(InpAutoDFloor && g_atrHandle != INVALID_HANDLE)
   {
      double atr[1];
      if(CopyBuffer(g_atrHandle, 0, 1, 1, atr) == 1 && atr[0] > 0)
         d = MathMax(d, atr[0] * InpDFloorAtrMult);
   }
   // 業者制約: TP距離(ρd)がストップレベルを下回るとTPを置けない → dを底上げ
   double stopsLv = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
   double dMin = (stopsLv * 1.5) / MathMax(InpRho, 0.1);
   if(d < dMin)
   {
      VLog(StringFormat("d=%.5f がストップレベル制約未満 → %.5f に底上げ", d, dMin));
      d = dMin;
   }
   return d;
}

//=== 発注プリミティブ ================================================
ENUM_ORDER_TYPE_FILLING PickFilling()
{
   long fm = SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((fm & SYMBOL_FILLING_IOC) != 0) return ORDER_FILLING_IOC;
   if((fm & SYMBOL_FILLING_FOK) != 0) return ORDER_FILLING_FOK;
   return ORDER_FILLING_RETURN;
}

void NoteOrderResult(const bool ok)
{
   if(ok) { g_consecErr = 0; return; }
   g_consecErr++;
   Print("[PGRID] 発注失敗 retcode=", trade.ResultRetcode(), " (", g_consecErr, "連続)");
   if(g_consecErr >= InpMaxConsecErr)
   {
      g_halt = HALT_ERRORS;
      Alert("[PGRID] 連続発注エラーでHALT: 状態を確認してください");
   }
}

// 新規約定 (ブリック方向, NORMAL=TP付き / TREND=TPなしトレイル)
bool OpenFill(const int dir, const double level, const ERole role, double lots)
{
   lots = NormalizeLots(lots);
   if(lots <= 0) return false;
   double tp = 0.0;
   if(role == ROLE_NORMAL)
      tp = NormalizeDouble(level + dir * InpRho * g_d, _Digits);
   bool ok;
   string cmt = (role == ROLE_TREND ? "PGE-T" : "PGE-N");
   if(dir > 0) ok = trade.Buy (lots, _Symbol, 0.0, 0.0, tp, cmt);
   else        ok = trade.Sell(lots, _Symbol, 0.0, 0.0, tp, cmt);
   ok = ok && (trade.ResultRetcode() == TRADE_RETCODE_DONE ||
               trade.ResultRetcode() == TRADE_RETCODE_DONE_PARTIAL);
   NoteOrderResult(ok);
   if(ok)
   {
      ulong ticket = trade.ResultOrder();   // ヘッジングではorder=positionチケット
      if(ticket > 0) RoleSet(ticket, role);
      int n = ArraySize(g_fillBricks);
      ArrayResize(g_fillBricks, n + 1);
      g_fillBricks[n] = g_brickCount;
      // オーバーシュート＝実効スリッページの実測 (RESEARCH_NOTE §8)
      double fillPx = trade.ResultPrice();
      if(fillPx > 0)
      {
         double half = SpreadPts() * 0.5;
         double ovs  = MathAbs(fillPx - level) / _Point - half; // 半スプレッドは想定内
         g_ovsSumPts += MathMax(ovs, 0.0);
         g_ovsN++;
      }
   }
   return ok;
}

bool ClosePosition(const ulong ticket, const string why)
{
   if(!PositionSelectByTicket(ticket)) return false;
   bool ok = trade.PositionClose(ticket, InpSlippagePts);
   ok = ok && (trade.ResultRetcode() == TRADE_RETCODE_DONE ||
               trade.ResultRetcode() == TRADE_RETCODE_DONE_PARTIAL);
   NoteOrderResult(ok);
   if(ok) { RoleDrop(ticket); VLog("close[" + why + "] #" + (string)ticket); }
   return ok;
}

//=== 全決済 (S7a: 損ポジ優先・中断機構・CloseBy・死体撃ち) ===========
struct SClose { ulong ticket; double pnl; long type; };

bool FullClose(const string reason, const bool interruptible)
{
   // スナップショット収集
   SClose arr[];
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0 || !IsOurs()) continue;
      int n = ArraySize(arr);
      ArrayResize(arr, n + 1);
      arr[n].ticket = t;
      arr[n].pnl    = PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
      arr[n].type   = PositionGetInteger(POSITION_TYPE);
   }
   // 含み損の深い順 (中断時に含み益が手元に残る順序: 設計 S7a)
   int n = ArraySize(arr);
   for(int i = 0; i < n - 1; i++)
      for(int j = i + 1; j < n; j++)
         if(arr[j].pnl < arr[i].pnl) { SClose tmp = arr[i]; arr[i] = arr[j]; arr[j] = tmp; }

   double minKeep = InpMinKeepFrac * InpThetaB * g_d * MoneyPerPriceUnit(InpLots);
   VLog(StringFormat("全決済開始[%s] %dポジ 盤面=%.2f", reason, n, BasketPnL()));

   for(int i = 0; i < n; i++)
   {
      if(!PositionSelectByTicket(arr[i].ticket)) continue;  // 既に消えた
      // CloseBy: 反対方向の残りとぶつけて片道スプレッド節約 (5.2 #2)
      bool done = false;
      if(g_closeByOK)
      {
         for(int j = i + 1; j < n && !done; j++)
         {
            if(arr[j].type == arr[i].type) continue;
            if(!PositionSelectByTicket(arr[j].ticket)) continue;
            if(trade.PositionCloseBy(arr[i].ticket, arr[j].ticket) &&
               (trade.ResultRetcode() == TRADE_RETCODE_DONE ||
                trade.ResultRetcode() == TRADE_RETCODE_DONE_PARTIAL))
            {
               RoleDrop(arr[i].ticket); RoleDrop(arr[j].ticket);
               arr[j].ticket = 0;
               done = true; g_consecErr = 0;
            }
         }
      }
      if(!done) ClosePosition(arr[i].ticket, reason);
      // 中断機構: バスケットTP中に逆行して最低確保を割ったら止める
      if(interruptible && BasketPnL() < minKeep && i < n - 1)
      {
         VLog(StringFormat("全決済中断: 盤面=%.2f < 最低確保=%.2f (残%dポジ継続管理)",
              BasketPnL(), minKeep, n - i - 1));
         return false;
      }
   }
   // 死体撃ち: 残ポジ再走査 (リクオート/拒否の取り残し)
   for(int retry = 0; retry < 3; retry++)
   {
      int remain = GrossCount();
      if(remain == 0) break;
      VLog(StringFormat("死体撃ち retry=%d 残=%d", retry + 1, remain));
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong t = PositionGetTicket(i);
         if(t > 0 && IsOurs()) ClosePosition(t, "corpse");
      }
      Sleep(300);
   }
   return GrossCount() == 0;
}

// 盤面リセット (4.4: アンカー再設定・勘定ゼロ・ガバナー再装填)
void BasketReset(const string why)
{
   VLog(StringFormat("盤面リセット[%s] 実現=%.2f ロック=%.2f", why,
        g_realizedSinceReset, g_lockedSinceReset));
   g_realizedSinceReset = 0.0;
   g_lockedSinceReset   = 0.0;
   g_basketHWM          = 0.0;
   ArrayResize(g_fillBricks, 0);
   ArrayResize(g_tpBricks, 0);
   g_anchor  = Mid();
   g_lastDir = 0;
   g_runLen  = 0;
   g_altChain= 0;
   g_mode    = MODE_NORMAL;
   g_d       = ComputeD(g_anchor);
   StateSave();
}

//=== ゲート群 (5.1) ==================================================
bool GateSpreadOK()      // E8: スプレッド異常で新規停止
{
   double sp = SpreadPts();
   if(g_spreadMed <= 0) { g_spreadMed = sp; return true; }
   g_spreadMed += (sp - g_spreadMed) * 0.001;   // ゆっくり追従する中央値近似
   return sp <= g_spreadMed * InpSpreadGateMult;
}

bool GateTickRateOK()    // E5: tick速度異常(R6)で新規停止
{
   return g_tickRate <= 0.0 ||
          (double)g_ticksThisSec <= MathMax(g_tickRate * InpTickRateMult, 10.0);
}

bool GateTimeOK()        // スワップ3倍日/時間帯 (5.2 #6)
{
   if(InpSwapPauseDay < 0) return true;
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   if(dt.day_of_week == InpSwapPauseDay && dt.hour >= InpSwapPauseHour) return false;
   return true;
}

int MarginStage()        // E7: 4段階 (0=通常 1=半減 2=停止 3=強制縮小)
{
   double u = MarginUsage();
   if(u >= InpMarginStage3) return 3;
   if(u >= InpMarginStage2) return 2;
   if(u >= InpMarginStage1) return 1;
   return 0;
}

//=== ガバナー (E1: 決済数連動・ブリック時間窓) =======================
void PruneWindow(long &arr[])
{
   int n = ArraySize(arr), k = 0;
   for(int i = 0; i < n; i++)
      if(arr[i] > g_brickCount - InpGovWindow) { arr[k++] = arr[i]; }
   ArrayResize(arr, k);
}

bool GovernorAllow()
{
   PruneWindow(g_fillBricks);
   PruneWindow(g_tpBricks);
   return ArraySize(g_fillBricks) <
          InpGovBase + InpGovAlpha * ArraySize(g_tpBricks);
}

//=== 滓処理 (4.1/4.3: 反転ブリックでの最悪滓クローズ) ================
// sim policy E と同義だが、新規を「消費」せず直接クローズ(往復1回分安い)
bool CloseWorstOrphan(const int brickDir, const double level)
{
   ulong worst = 0; double worstGap = 0.0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0 || !IsOurs()) continue;
      long ptype = PositionGetInteger(POSITION_TYPE);
      int pdir = (ptype == POSITION_TYPE_BUY) ? +1 : -1;
      if(pdir == brickDir) continue;                      // 同方向は対象外
      double open = PositionGetDouble(POSITION_PRICE_OPEN);
      double gap  = pdir * (open - level);                // 逆行幅
      if(gap >= g_d * 0.9 && gap > worstGap) { worstGap = gap; worst = t; }
   }
   if(worst == 0) return false;
   bool ok = ClosePosition(worst, "orphan");
   if(ok)
   {
      g_lockedSinceReset += -(worstGap) * MoneyPerPriceUnit(InpLots); // 参考値
      g_nOrphan++;
   }
   return ok;
}

//=== トレイル決済 (S1/S7b: 逆方向ブリックでTRENDポジを閉じる) ========
void TrailExits(const int brickDir)
{
   ulong list[];
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0 || !IsOurs()) continue;
      int pdir = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? +1 : -1;
      if(pdir != brickDir && RoleGet(t) == ROLE_TREND)
      {
         int n = ArraySize(list);
         ArrayResize(list, n + 1);
         list[n] = t;
      }
   }
   for(int i = 0; i < ArraySize(list); i++)
      if(ClosePosition(list[i], "trail")) g_nTrail++;
}

//=== バスケット判定 (5.2 #0,#1 + 4.4) ================================
void CheckBasket(const bool tickLevelEmergencyOnly)
{
   double mpu     = MoneyPerPriceUnit(InpLots);
   double basket  = BasketPnL();
   double target  = InpThetaB  * g_d * mpu;
   double budget  = InpBudgetD * g_d * mpu;
   if(basket > g_basketHWM) g_basketHWM = basket;

   if(tickLevelEmergencyOnly)
   {
      // simはブリックレベル判定。tickレベルは緊急安全弁のみ(検証外領域を明示)
      if(GrossCount() > 0 && basket <= -budget * InpEmgStopMult)
      {
         VLog(StringFormat("緊急予算ストップ(tickレベル) 盤面=%.2f", basket));
         if(FullClose("emergency-stop", false))
         { g_nBudgetStop++; BasketReset("emergency"); }
      }
      return;
   }
   if(GrossCount() == 0) return;

   bool takeTP = false;
   if(InpBasketTrail)
   {
      // 盤面トレイリング: 目標到達で武装→HWMからの押しで発射 (V字防衛: S7c)
      if(g_basketHWM >= target && basket <= g_basketHWM * InpBasketKeep)
         takeTP = true;
   }
   else if(basket >= target) takeTP = true;

   if(takeTP)
   {
      if(FullClose("basket-TP", true)) { g_nBasketTP++; BasketReset("basketTP"); }
      else StateSave();   // 中断: 残存ポジで継続(勘定は実現分が更新済み)
      return;
   }
   if(basket <= -budget)
   {
      VLog(StringFormat("予算ストップ 盤面=%.2f <= -%.2f (小さく死ぬ制度)", basket, budget));
      if(FullClose("budget-stop", false)) { g_nBudgetStop++; BasketReset("budget"); }
   }
}

//=== ブリックイベント処理 (simulate.py のメインループと同順) =========
void OnBrick(const int b, const double level)
{
   g_brickCount++;

   // --- ラン/交互連鎖の更新 (6.3: 自己約定列センサー)
   if(b == g_lastDir) g_runLen++; else g_runLen = 1;
   if(g_lastDir != 0)
   {
      g_transTotal++;
      if(b == g_lastDir) g_transSame++;
   }
   g_lastDir  = b;
   g_altChain = (g_runLen == 1) ? g_altChain + 1 : 0;

   // --- トレイル決済 (新規より先: simステップ2)
   TrailExits(b);

   // --- モード判定 (6.2)
   EMode prev = g_mode;
   if(g_runLen >= InpTrailAfter)      g_mode = MODE_TREND;
   else if(g_altChain >= InpChopAlt)  g_mode = MODE_CHOP;
   else                               g_mode = MODE_NORMAL;
   if(g_mode != prev) VLog(StringFormat("モード遷移 %d→%d (run=%d alt=%d)",
                            prev, g_mode, g_runLen, g_altChain));

   // --- 参加可否 (E: TREND=無条件 / CHOP=棄権 / NORMAL=ガバナー)
   bool allow;
   if(g_mode == MODE_TREND)      allow = true;
   else if(g_mode == MODE_CHOP)  allow = false;
   else                          allow = GovernorAllow();

   // --- 外側ゲート (L5は全レイヤーに優先: 6.1)
   int mstage = MarginStage();
   bool gates = (g_halt == HALT_NONE) && GateSpreadOK() && GateTickRateOK() &&
                GateTimeOK() && (mstage < 2) && (GrossCount() < InpMaxGross);
   if(!gates) allow = false;

   // --- 反転ブリックでの滓処理 (sim E: CHOP直接クローズ / NORMAL消費相当)
   bool recycled = false;
   if(g_runLen == 1 && g_mode != MODE_TREND && g_halt == HALT_NONE)
      recycled = CloseWorstOrphan(b, level);

   // --- 新規約定 (滓処理した反転ブリックでは新規を打たない=sim「消費」対応)
   if(allow && !recycled)
   {
      double lots = (mstage == 1) ? InpLots * 0.5 : InpLots;
      ERole role  = (g_mode == MODE_TREND) ? ROLE_TREND : ROLE_NORMAL;
      OpenFill(b, level, role, lots);
   }
   else if(!allow) g_gatedBricks++;

   // --- 強制縮小 (E7 stage3: 最悪ポジから盤面を軽くする)
   if(mstage >= 3)
   {
      VLog("証拠金stage3: 強制縮小");
      CloseWorstOrphan(b, level);
   }

   // --- バスケット判定 (simステップ6: ブリックレベル)
   CheckBasket(false);
   StateSave();
}

//=== ブリック検出 (RenkoBuilder.update 相当, mid基準: v0.2確定事項5) =
void UpdateBricks()
{
   double mid = Mid();
   if(g_anchor <= 0) { g_anchor = mid; g_d = ComputeD(mid); return; }

   // 同期異常を先に判定: 週末ギャップ/接続断で大きく乖離した場合、
   // 古い水準への連続発注を防ぎ、取引せずに再アンカーする (S6/8章)
   if(MathAbs(mid - g_anchor) > 50.0 * g_d)
   {
      Alert("[PGRID] アンカー同期異常(ギャップ) → 取引せず再アンカー");
      g_anchor = mid;
      g_d      = ComputeD(mid);
      g_runLen = 0; g_altChain = 0; g_lastDir = 0;
      StateSave();
      return;
   }

   for(int guard = 0; guard < MAX_BRICKS_PER_TICK; guard++)
   {
      double upTh = g_anchor + ((g_lastDir >= 0) ? g_d : InpRevMult * g_d);
      double dnTh = g_anchor - ((g_lastDir <= 0) ? g_d : InpRevMult * g_d);
      int    b    = 0;
      double lvl  = 0.0;
      if(mid >= upTh)      { b = +1; lvl = upTh; }
      else if(mid <= dnTh) { b = -1; lvl = dnTh; }
      else break;

      g_anchor = lvl;
      g_d      = ComputeD(g_anchor);
      OnBrick(b, lvl);
   }
}

//=== ダッシュボード (9章: 可視化文化) ================================
void Dashboard()
{
   double mpu    = MoneyPerPriceUnit(InpLots);
   double basket = BasketPnL();
   double pHat   = (g_transTotal > 20) ? (double)g_transSame / g_transTotal : 0.0;
   // χ_eff = (スプレッド + 2×片道手数料 + 実測オーバーシュート) / d  (v0.2規律: <0.10)
   double commPx = (mpu > 0) ? 2.0 * InpCommPerLotSide * InpLots / mpu : 0.0;
   double ovsPx  = (g_ovsN > 0) ? (g_ovsSumPts / g_ovsN) * _Point : 0.0;
   double chiEff = (g_d > 0) ? (SpreadPts() * _Point + commPx + ovsPx) / g_d : 0.0;
   string mode = (g_mode == MODE_TREND) ? "TREND" : (g_mode == MODE_CHOP ? "CHOP" : "NORMAL");
   if(g_halt != HALT_NONE) mode = "HALT";
   Comment(StringFormat(
      "PGRID-E v0.2 | %s | mode=%s\n"
      "anchor=%.5f d=%.5f (%.1f bps) rev=%.1fd run=%d alt=%d brick#%I64d\n"
      "盤面=%.2f (実現=%.2f 含み=%.2f) HWM=%.2f | 目標=%.2f 予算=-%.2f\n"
      "gross=%d/%d | TP=%d trail=%d orphan=%d bTP=%d stop=%d | 棄権=%I64d\n"
      "p̂(継続)=%.3f | χ_eff≈%.3f (目標<0.10) | spread=%.0fpt(med %.0f) | margin=%.0f%% stage=%d",
      _Symbol, mode,
      g_anchor, g_d, (g_anchor > 0 ? g_d / g_anchor * 1e4 : 0), InpRevMult,
      g_runLen, g_altChain, g_brickCount,
      basket, g_realizedSinceReset, FloatingPnL(), g_basketHWM,
      InpThetaB * g_d * mpu, InpBudgetD * g_d * mpu,
      GrossCount(), InpMaxGross,
      g_nTP, g_nTrail, g_nOrphan, g_nBasketTP, g_nBudgetStop, g_gatedBricks,
      pHat, chiEff, SpreadPts(), g_spreadMed, MarginUsage() * 100.0, MarginStage()));
}

//=== イベントハンドラ ================================================
int OnInit()
{
   // 口座前提 (8章 #1): ヘッジングモード必須
   if((ENUM_ACCOUNT_MARGIN_MODE)AccountInfoInteger(ACCOUNT_MARGIN_MODE)
      != ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
   {
      Alert("[PGRID] ヘッジング口座が必要です(ネッティング不可)");
      return INIT_FAILED;
   }
   // 多重起動ガード (8章 #9)
   g_lockGV = "PGRID_LOCK_" + _Symbol + "_" + (string)InpMagic;
   if(!MQLInfoInteger(MQL_TESTER))
   {
      if(GlobalVariableCheck(g_lockGV) && GlobalVariableGet(g_lockGV) > 0.5)
      {
         Alert("[PGRID] 同一シンボル+マジックのEAが既に稼働中の可能性");
         return INIT_FAILED;
      }
      GlobalVariableSet(g_lockGV, 1.0);
   }
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePts);
   trade.SetTypeFilling(PickFilling());
   trade.SetAsyncMode(false);

   g_closeByOK = (SymbolInfoInteger(_Symbol, SYMBOL_ORDER_MODE) & SYMBOL_ORDER_CLOSEBY) != 0;
   g_atrHandle = iATR(_Symbol, PERIOD_H1, 24);
   g_stateFile = "PGRID_" + _Symbol + "_" + (string)InpMagic + ".state";

   // リシンク (8章 #8): 状態ファイル + 実ポジの突き合わせ
   bool loaded = StateLoad();
   int  gross  = GrossCount();
   if(!loaded)
   {
      g_anchor = Mid();
      g_d      = ComputeD(g_anchor);
      if(gross > 0)
         VLog(StringFormat("状態ファイルなし+既存%dポジ → ポジを引き継ぎ新規アンカー", gross));
   }
   else
      VLog(StringFormat("状態復元: anchor=%.5f brick#%I64d gross=%d", g_anchor, g_brickCount, gross));

   EventSetTimer(5);
   VLog(StringFormat("起動: d=%.5f closeBy=%s filling=%d", g_d,
        g_closeByOK ? "対応" : "非対応(個別決済で代替)", (int)PickFilling()));
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   StateSave();
   EventKillTimer();
   if(!MQLInfoInteger(MQL_TESTER)) GlobalVariableSet(g_lockGV, 0.0);
   Comment("");
}

void OnTick()
{
   // tick速度の計測 (E5)
   datetime sec = TimeCurrent();
   if(sec != g_lastTickSec)
   {
      if(g_lastTickSec > 0)
         g_tickRate += ((double)g_ticksThisSec - g_tickRate) * 0.02;
      g_lastTickSec = sec;
      g_ticksThisSec = 0;
   }
   g_ticksThisSec++;

   if(g_halt == HALT_ACCOUNT) { Dashboard(); return; }

   // 金曜フラット (任意, XAU週末ギャップ: S6)
   if(InpFriFlattenHour >= 0)
   {
      MqlDateTime dt;
      TimeToStruct(TimeCurrent(), dt);
      if(dt.day_of_week == 5 && dt.hour >= InpFriFlattenHour && GrossCount() > 0)
      {
         if(FullClose("friday-flat", false)) BasketReset("friday");
         Dashboard();
         return;
      }
   }

   UpdateBricks();            // ブリック判定→OnBrick(政策パイプライン)
   CheckBasket(true);         // tickレベルは緊急安全弁のみ
   Dashboard();
}

// 約定イベント: 実現勘定の更新とTP検出 (E1ガバナーの燃料)
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD) return;
   if(!HistoryDealSelect(trans.deal)) return;
   if(HistoryDealGetInteger(trans.deal, DEAL_MAGIC) != InpMagic) return;
   if(HistoryDealGetString(trans.deal, DEAL_SYMBOL) != _Symbol) return;

   long entry = HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
   double amt = HistoryDealGetDouble(trans.deal, DEAL_PROFIT)
              + HistoryDealGetDouble(trans.deal, DEAL_SWAP)
              + HistoryDealGetDouble(trans.deal, DEAL_COMMISSION)
              + HistoryDealGetDouble(trans.deal, DEAL_FEE);

   if(entry == DEAL_ENTRY_IN)
   {
      g_realizedSinceReset += amt;   // 建玉手数料分
      return;
   }
   if(entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_OUT_BY)
   {
      g_realizedSinceReset += amt;
      ulong posId = (ulong)HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID);
      if(!PositionSelectByTicket(posId)) RoleDrop(posId);   // 全量決済済み
      if(HistoryDealGetInteger(trans.deal, DEAL_REASON) == DEAL_REASON_TP)
      {
         g_nTP++;
         int n = ArraySize(g_tpBricks);          // ガバナー窓へTPを記帳
         ArrayResize(g_tpBricks, n + 1);
         g_tpBricks[n] = g_brickCount;
      }
   }
}

void OnTimer()
{
   // 定期ハウスキーピング: 取引許可・状態保存
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) ||
      !MQLInfoInteger(MQL_TRADE_ALLOWED))
   {
      if(g_halt == HALT_NONE) VLog("取引不許可状態を検知(自動売買ボタン/サーバー)");
      return;
   }
   StateSave();
}
//+------------------------------------------------------------------+
