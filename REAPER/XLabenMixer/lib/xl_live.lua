-- 試聴用の JSFX（A2M_LivePreview.jsfx）へ、共有メモリでメッセージを渡す。番号の意味は JSFX の先頭の説明と同じ。
local r = reaper
local M = {}

M.NS = "A2M_LIVE"
M.VERSION = 12
M.CCB = 300000      -- CC の表（チャンネル 0〜15 × 128。-1 = リセットのあとの既定のまま）
M.NRB, M.RPB, M.SELB = 500000, 520000, 521000   -- NRPN の値、RPN の値、選んでいるもの（JSFX の説明と同じ）
M.BASE, M.SLOTS, M.SLOT = 128, 4096, 72
M.VEL, M.NOTES = 48, 64

local attached = false
local function attach()
    if not attached then
        r.gmem_attach(M.NS)
        attached = true
    end
end

-- まだ JSFX が送っていない数
function M.pending()
    attach()
    return r.gmem_read(0) - r.gmem_read(1)
end

-- 1件送る（文字列）。delay_ms は、前のメッセージを送ってから、これを送るまで待つ時間。入る場所がなければ false
function M.send(msg, delay_ms)
    attach()
    local n = #msg
    if n < 1 or n > M.SLOT - 2 then return false end
    local w = r.gmem_read(0)
    if w - r.gmem_read(1) >= M.SLOTS then return false end
    local s = M.BASE + (w % M.SLOTS) * M.SLOT
    r.gmem_write(s, n)
    r.gmem_write(s + 1, delay_ms or 0)
    for i = 1, n do r.gmem_write(s + 1 + i, msg:byte(i)) end
    r.gmem_write(0, w + 1)
    return true
end

-- まとめて送る。list は {msg, delay_ms} の並び。入らなかった分（first から）の番号を返す（全部入れば nil）
function M.send_all(list, first)
    for i = first or 1, #list do
        if not M.send(list[i][1], list[i][2]) then return i end
    end
    return nil
end

function M.cc(ch, num, value)
    return M.send(string.char(0xB0 | (ch - 1), num, math.max(0, math.min(127, value))), 0)
end

-- JSFX が動いているかを見るための印（ブロックごとに増える）
function M.heartbeat()
    attach()
    return r.gmem_read(2)
end

function M.version()
    attach()
    return r.gmem_read(4)
end

-- つまんでいる間、トラックから来るその CC を止める（ch = nil で解除）
function M.hold_cc(ch, num)
    attach()
    r.gmem_write(81, num or 0)
    r.gmem_write(80, ch or 0)
end

-- 音源に入った、ふつうの CC 以外のメッセージの数（JSFX が数える）
function M.structure_count()
    attach()
    return r.gmem_read(6)
end

-- JSFX が始まった印（読み直されると変わる）
function M.session()
    attach()
    return r.gmem_read(9)
end

-- 音源に入っている CC の値（-1 = リセットのあとの既定のまま）
function M.cc_value(ch, num)
    attach()
    return r.gmem_read(M.CCB + (ch - 1) * 128 + num)
end

-- 覚える NRPN の枠（MSB 1 → 0、24〜31 → 1〜8、ほか → nil）
local function nrpn_slot(msb)
    if msb == 1 then return 0 elseif msb and msb >= 24 and msb <= 31 then return msb - 23 end
end

-- 音源に入っている RPN / NRPN の値（mode = "rpn" / "nrpn"）。-1 = 分からない、nil = JSFX が覚えないパラメーター
function M.param_value(ch, mode, msb, lsb)
    attach()
    if mode == "nrpn" then
        local slot = nrpn_slot(msb)
        if not slot or not lsb then return nil end
        return r.gmem_read(M.NRB + ((ch - 1) * 9 + slot) * 128 + lsb)
    elseif mode == "rpn" and msb == 0 and lsb and lsb >= 0 and lsb <= 2 then
        return r.gmem_read(M.RPB + (ch - 1) * 3 + lsb)
    end
end

-- 音源で今選んでいる RPN / NRPN：mode（"rpn" / "nrpn" / nil = 分からない）, msb, lsb
function M.selection(ch)
    attach()
    local k = M.SELB + (ch - 1) * 3
    local m = r.gmem_read(k)
    local mode = m == 1 and "rpn" or m == 2 and "nrpn" or nil
    return mode, r.gmem_read(k + 1), r.gmem_read(k + 2)
end

-- (最後のノートオンのベロシティ, ノートオンの数)
function M.meter(ch)
    attach()
    return r.gmem_read(M.VEL + ch - 1), r.gmem_read(M.NOTES + ch - 1)
end

return M
