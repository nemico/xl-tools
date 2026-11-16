-- @description X-Laben 先に送っておく（止まっている間、カーソルの位置の状態を音源へ。Space をすぐ再生にする）
-- @version 1.0.0
--
-- 裏で動き続ける。止まっている間に、次のどれかが変わったら、編集カーソルの位置で音源がなっているはずの状態
-- （CC・プログラム・SysEx の最後の値）を X-Laben トラックの JSFX から送り直し、落ち着く時間（既定 400 ms）も先に待っておく：
--   ・編集カーソル（シーク）  ・MIDI の編集、元に戻す、ミキサー・EFXUI の書き込み
--   ・音源に入ったもの（ミキサー・EFXUI が送った、再生した、JSFX が読み直された）
-- 準備ができていれば、Space（XLaben_Play）は何も送らずにすぐ再生する。送っている途中なら、準備ができるのを待って再生する。
-- 再生中は何もしない。
-- 起動：Space（XLaben_Play）かミキサーを初めて使ったときに、自動で起動する（アクションにも登録される）。
-- 止める：もう一度実行する（REAPER を起動し直すまで）。ずっと使わないときは、ミキサーの右クリックで切る。

local DIR = debug.getinfo(1, "S").source:match("^@?(.*[\\/])")
local r = reaper
if r.set_action_options then r.set_action_options(1 | 4) end  -- もう一度実行したら止める。動いている間はトグル点灯
local sync = dofile(DIR .. "lib/xl_sync.lua")
local watch = dofile(DIR .. "lib/xl_watch.lua")

local function off()
    sync.clear()
    if r.set_action_options then r.set_action_options(8) end
end
r.atexit(off)
watch.run(DIR, off)
