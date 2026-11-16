-- 「先に送っておく」（XLaben_Sync.lua）と、再生（xl_play）が共有する決まり。
-- XLaben_Sync は、止まっている間、編集カーソルの位置で音源がなっているはずの状態を先に送り、落ち着く時間も先に待っておく。
-- 送り終わって落ち着いたら、そのときの「今の状況の鍵」（fast_key）と時刻を ExtState に出す。
-- Space（xl_play）は、今の鍵が出ている鍵と同じで、落ち着いた時刻を過ぎていれば、何も送らずにすぐ再生する。
local r = reaper
local DIR = debug.getinfo(1, "S").source:match("^@?(.*[\\/])")
local live = dofile(DIR .. "xl_live.lua")

local M = {}

M.EXT = "XLabenMixer"
M.ALIVE_S = 3.0          -- この間に印が書かれなければ、XLaben_Sync は動いていない（読むのに時間がかかる曲でも切れないように）
M.HOLD_OFF_S = 1.0       -- 再生のスクリプトが自分で送っている印が、この間に書かれていれば、XLaben_Sync は送らない
M.SCRIPT = "XLaben_Sync.lua"
-- Space と XLaben_Sync の決まりの版。スクリプトはコードを起動したときに読むので、直したあとも前の XLaben_Sync が
-- 動き続けていることがある。版が違えば、Space（launch）が前のものを止めて起動し直す。決まりを変えたら上げる
M.VERSION = 8

-- 先に送っておくか（既定はする。ミキサーの右クリックで切り替える）
function M.enabled() return r.GetExtState(M.EXT, "presync") ~= "0" end
function M.set_enabled(on) r.SetExtState(M.EXT, "presync", on and "1" or "0", true) end

-- 音源がなっているはずの状態が変わりうるもの：プロジェクト、プロジェクトの変更の数（編集・元に戻すで増える）、編集カーソル
function M.target_key()
    return string.format("%s|%d|%.9f", tostring(r.EnumProjects(-1)), r.GetProjectStateChangeCount(0), r.GetCursorPosition())
end

-- 音源に入っているものが変わりうるもの：JSFX が始まった印、ふつうの CC 以外が入った数、スクリプトが送った数
-- （ミキサーや EFXUI が送っても、再生してトラックの MIDI が入っても変わる。再生中のことは XLaben_Sync が見る）
function M.inst_key()
    return string.format("%s|%s|%s", tostring(live.session()), tostring(live.structure_count()), tostring(r.gmem_read(0)))
end

function M.fast_key() return M.target_key() .. "#" .. M.inst_key() end

-- XLaben_Sync が出しているもの
function M.published()
    local g = function(k) return r.GetExtState(M.EXT, k) end
    return {fp = g("sync_fp"), ready_at = tonumber(g("sync_ready_at")) or math.huge,
            alive = tonumber(g("sync_alive")) or -math.huge, error = g("sync_error"), how = g("sync_how"),
            running = g("sync_running") == "1" and g("sync_version") == tostring(M.VERSION)}
end

-- Space が準備を待っている（止めた直後の待ち・カーソルを動かしたあとの間を飛ばして、すぐ送ってほしい）
function M.set_urgent(on)
    if on then r.SetExtState(M.EXT, "sync_urgent", "1", false) else r.DeleteExtState(M.EXT, "sync_urgent", false) end
end
function M.urgent() return r.GetExtState(M.EXT, "sync_urgent") == "1" end

-- 再生のスクリプトが自分で送っている（XLaben_Sync が動いていなかったときなど）。その間 XLaben_Sync は送らない
function M.hold_off(now) r.SetExtState(M.EXT, "play_sending", string.format("%.6f", now), false) end
function M.held_off(now) return now - (tonumber(r.GetExtState(M.EXT, "play_sending")) or -math.huge) < M.HOLD_OFF_S end

function M.publish(fp, ready_at, how)
    r.SetExtState(M.EXT, "sync_fp", fp or "", false)
    r.SetExtState(M.EXT, "sync_ready_at", ready_at and string.format("%.6f", ready_at) or "", false)
    r.SetExtState(M.EXT, "sync_how", how or "", false)
end

function M.set_error(e) r.SetExtState(M.EXT, "sync_error", e or "", false) end
function M.beat(now) r.SetExtState(M.EXT, "sync_alive", string.format("%.6f", now), false) end
function M.alive(info, now) return info.running and now - info.alive < M.ALIVE_S end
function M.set_running(on)
    r.SetExtState(M.EXT, "sync_running", on and "1" or "", false)
    r.SetExtState(M.EXT, "sync_version", on and tostring(M.VERSION) or "", false)
end

-- 止めたとき：印を消す（Space が古い印で再生しないように）
function M.clear()
    for _, k in ipairs({"sync_fp", "sync_ready_at", "sync_alive", "sync_error", "sync_how", "sync_running", "sync_urgent", "sync_version"}) do
        r.DeleteExtState(M.EXT, k, false)
    end
end

-- XLaben_Sync.lua を起動する（アクションに登録して実行）。もう動いていれば何もしない。
-- 動いているのが前の版なら、止めて（もう一度実行すると止まる）、次の回で起動し直す。起動し直したら "restarted"
function M.launch(script_root)
    if not M.enabled() or not r.AddRemoveReaScript then return false end
    local path = script_root .. M.SCRIPT
    local f = io.open(path, "rb")
    if not f then return false end
    f:close()
    local cmd = r.AddRemoveReaScript(true, 0, path, true)
    if not cmd or cmd <= 0 then return false end
    if r.GetToggleCommandState(cmd) ~= 1 then
        r.Main_OnCommand(cmd, 0)
        return true
    end
    if r.GetExtState(M.EXT, "sync_version") == tostring(M.VERSION) then return true end
    r.Main_OnCommand(cmd, 0)  -- 前の版を止める
    r.defer(function()
        if r.GetToggleCommandState(cmd) ~= 1 then r.Main_OnCommand(cmd, 0) end
    end)
    r.SetExtState(M.EXT, "last_play", "前の版の XLaben_Sync が動いていたので、起動し直しました（今回は Space が自分で送ります）", false)
    return "restarted"
end

return M
