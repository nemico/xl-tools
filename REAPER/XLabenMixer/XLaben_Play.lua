-- @description X-Laben 再生・停止（Space に割り当てる。FastPreroll の代わり）
-- @version 1.4.0
--
-- 先に送っておく（XLaben_Sync.lua。初めて押したときに自動で起動する）が、止まっている間にカーソルの位置の状態を送って
--   落ち着くまで待っておくので、準備ができていれば、何も送らずにすぐ再生する。送っている途中なら、準備ができてから再生する。
-- XLaben_Sync が動いていない・切ってあるとき：編集カーソルの位置で音源がなっているはずの状態（CC・プログラム・SysEx の最後の値）を、
--   X-Laben トラックの JSFX（A2M Live MIDI preview）から音源へ送ってから、その位置から再生する。
--   ・音源にもう入っている状態と比べ、違うのがふつうの CC（Expression・パン・送りなど）だけなら、それだけを送ってすぐ再生する。
--     （JSFX が音源に入ったものを全部見ていて、前に全部を送ってから SysEx・音色・RPN/NRPN などが何も入っていないときだけ）
--   ・そうでなければ全部を 1ms ずつ間をあけて送り、落ち着く時間（既定 400ms。ミキサーの右クリックで変えられる）がたってから再生する。
--   停止中に JSFX が動いていなければ、違う音を鳴らさないように再生しない（理由を出す）。
--   一時アイテムを作らない。前に戻って再生し直さない。初回は JSFX を X-Laben トラックの先頭に入れる。
-- 前から再生する長さ（ミキサーの右クリック）を決めると、押したらすぐカーソルの少し前から再生を始め、送る・落ち着くのを
--   再生と重ねる。カーソルの位置までは音を出さない（マスターの A2M Preroll gate）。
-- 再生中：止める。送っている最中にもう一度押すと取り消す。
-- 使い方：アクションリストでこのスクリプトを読み込み、Space キーに割り当てる。

local DIR = debug.getinfo(1, "S").source:match("^@?(.*[\\/])")
if reaper.set_action_options then reaper.set_action_options(1) end  -- 待っている間にもう一度押されたら、取り消す
local play = dofile(DIR .. "lib/xl_play.lua")
-- 待っている最中にもう一度押されて止められたら、XLaben_Sync への「急いで」を取り消す
reaper.atexit(function() if play.busy() then reaper.DeleteExtState("XLabenMixer", "sync_urgent", false) end end)
play.toggle(DIR)
