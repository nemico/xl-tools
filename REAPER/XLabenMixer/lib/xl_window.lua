-- 窓の開く・閉じる・切り替え（ミキサー・ドラム・EFXUI で共通）。
-- それぞれのスクリプトに割り当てたショートカット（やツールバーのボタン）を押すと：
--   ・窓が見えているとき（浮いている窓、ドッキングの見えているタブ）→ 閉じる
--   ・窓が見えていないとき（ドッキングの裏のタブ）→ 前に出す（切り替える）
--   ・開いていないとき → 開く
-- しくみ：set_action_options(1 | 2) で、動いている最中にもう一度実行されたら、今のものを止めて新しく起動し直す。
-- 止まるときに「見えていたか」を ExtState に残し、新しく起動したほうはそれを読んで、見えていたなら開かずに終わる（= 閉じる）。
-- 見えていなかったなら、同じドッキングの場所に開き直す（新しく開いたタブが前に出る）。
-- 窓の様子は gfx.getchar(65536)：1 = 使える、2 = 入力を受けている、4 = 見えている（REAPER 5.96 から）
local r = reaper

local M = {}
M.RELAUNCH_S = 1.0   -- 止まってから、この間に起動されたら「もう一度押された」とみなす

local visible = true  -- 最後に見たときに見えていたか（調べられないときは見えているとみなす＝今までどおり閉じる）
local me = nil        -- 自分のアクションの番号
local ext_name = nil

-- 同じ窓を開くアクションが2つあるとき（ドラムの SC_Drum_Macro.lua と XLaben_Drum.lua など）：
-- 別のアクションの窓がもう動いていれば、そちらのアクションを実行して（閉じる・前に出す）、こちらは開かない

-- 起動したときに呼ぶ。開くなら true。false なら、すぐ終わる（もう一度押されて閉じるとき）
function M.start(ext)
    if r.set_action_options then r.set_action_options(1 | 2 | 4) end
    ext_name = ext
    me = select(4, r.get_action_context())
    local run = r.GetExtState(ext, "running")
    local rcmd, rt = run:match("^(%-?%d+)|([%d%.]+)$")
    rcmd, rt = tonumber(rcmd), tonumber(rt)
    if rcmd and rt and rcmd ~= me and rcmd > 0 and r.time_precise() - rt < M.RELAUNCH_S then
        if r.set_action_options then r.set_action_options(8) end
        r.Main_OnCommand(rcmd, 0)
        return false
    end
    local s = r.GetExtState(ext, "exit_info")
    r.DeleteExtState(ext, "exit_info", false)
    local t, vis = s:match("^([%d%.]+)|(%d)$")
    if t and r.time_precise() - tonumber(t) < M.RELAUNCH_S and vis == "1" then
        if r.set_action_options then r.set_action_options(8) end
        return false
    end
    return true
end

-- 1コマごとに呼ぶ（gfx.init のあと）
function M.track()
    if ext_name then r.SetExtState(ext_name, "running", string.format("%d|%.3f", me or 0, r.time_precise()), false) end
    local f = gfx.getchar(65536)
    if f and f & 1 == 1 then visible = f & 4 == 4 else visible = true end
    return visible
end

-- 止まるとき（atexit）に呼ぶ
function M.finish(ext)
    r.DeleteExtState(ext, "running", false)
    r.SetExtState(ext, "exit_info", string.format("%.3f|%d", r.time_precise(), visible and 1 or 0), false)
    if r.set_action_options then r.set_action_options(8) end
end

-- テスト用：今の「見えているか」
function M.visible() return visible end

return M
