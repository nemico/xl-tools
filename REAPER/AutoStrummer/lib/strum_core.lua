-- ストラムの計算（REAPER を使わない）。時刻はすべて秒。
-- ノート：{start, stop, pitch, chan, vel}（start・stop は秒）
local M = {}

-- 既定の値と、動かせる範囲
M.DEFAULTS = {offsetMs = 20, minVel = 80, maxVel = 100, globalShift = 0, offShiftMs = 0, brushLenMs = 20,
              upStrum = false, moveNoteOff = false, brushing = false}
M.RANGES = {offsetMs = {0, 400}, minVel = {1, 127}, maxVel = {1, 127}, globalShift = {-127, 127},
            offShiftMs = {-400, 400}, brushLenMs = {1, 400}}
M.TOL_S = 0.002   -- これより小さい差は「同じ」とみなす（2 ms）

local function clamp(v, lo, hi) return math.max(lo, math.min(hi, v)) end
local function round(v) return math.floor(v + 0.5) end

-- 値をそろえる：範囲に収め、整数にし、Min ≤ Max にする
function M.normalize(p)
    local out = {}
    for k, v in pairs(M.DEFAULTS) do
        local x = p[k]
        if x == nil then x = v end
        local rg = M.RANGES[k]
        if rg then x = clamp(round(tonumber(x) or v), rg[1], rg[2]) else x = x and true or false end
        out[k] = x
    end
    if out.minVel > out.maxVel then out.minVel = out.maxVel end
    return out
end

-- 和音に分ける：始まりの順に並べ、それまでの和音のどれかの音が鳴っている間に始まるノートは同じ和音。
-- 戻り値：和音の並び（それぞれノートの並び。元のノートの表をそのまま入れる）
function M.chords(notes)
    local sorted = {}
    for i, n in ipairs(notes) do sorted[i] = n end
    table.sort(sorted, function(a, b)
        if a.start ~= b.start then return a.start < b.start end
        return a.pitch < b.pitch
    end)
    local out, cur, cur_end = {}, nil, -math.huge
    for _, n in ipairs(sorted) do
        if cur and n.start < cur_end - 1e-9 then
            cur[#cur + 1] = n
            if n.stop > cur_end then cur_end = n.stop end
        else
            cur = {n}
            out[#out + 1] = cur
            cur_end = n.stop
        end
    end
    return out
end

-- 和音の中の弾く順：Down は低い音から、Up は高い音から（同じ高さは始まりの順）
local function order(chord, up)
    local o = {}
    for i, n in ipairs(chord) do o[i] = n end
    table.sort(o, function(a, b)
        if a.pitch ~= b.pitch then
            if up then return a.pitch > b.pitch end
            return a.pitch < b.pitch
        end
        if a.start ~= b.start then return a.start < b.start end
        return (a.chan or 0) < (b.chan or 0)
    end)
    return o
end

-- 新しい位置と強さ：{[ノート] = {start, stop, vel}}。p は normalize したもの。
--   始まり：和音のいちばん早い始まりから、弾く順に Offset ずつ遅らせる
--   強さ：弾く順に Max → Min（1 音だけなら Min）、それに Global Shift を足す
--   終わり：Brushing なら始まりから Length。Move Note Off なら和音のいちばん長い長さ ＋ Note Off Shift。
--          それ以外は元の終わり ＋ Note Off Shift
function M.plan(notes, p)
    local out = {}
    for _, chord in ipairs(M.chords(notes)) do
        local o = order(chord, p.upStrum)
        local n = #o
        local anchor, longest = math.huge, 0
        for _, x in ipairs(chord) do
            anchor = math.min(anchor, x.start)
            longest = math.max(longest, x.stop - x.start)
        end
        local maxV = n == 1 and p.minVel or p.maxVel
        for k, x in ipairs(o) do
            local t = n > 1 and (k - 1) / (n - 1) or 0
            local vel = clamp(round(maxV + (p.minVel - maxV) * t) + p.globalShift, 1, 127)
            local start = anchor + (k - 1) * p.offsetMs / 1000
            local stop
            if p.brushing then
                stop = start + p.brushLenMs / 1000
            elseif p.moveNoteOff then
                stop = start + longest + p.offShiftMs / 1000
            else
                stop = x.stop + p.offShiftMs / 1000
            end
            out[x] = {start = start, stop = stop, vel = vel}
        end
    end
    return out
end

-- 選んだノート（最初の和音）が、もうストラムしてある（始まりがずれている）なら、その弾き方を読む：
-- Offset（弾く順の間の真ん中の値）、Min / Max、Up / Down、Move Note Off（終わりがそろっていなければ On）。
-- そろって始まる和音（まだストラムしていない）なら、cur（今の値）のまま（Offset 0・強さが全部同じ、にはしない）
function M.infer(notes, cur)
    local p = {}
    for k, v in pairs(cur) do p[k] = v end
    local chord = M.chords(notes)[1]
    if not chord or #chord < 2 then return p end
    local s_lo, s_hi = math.huge, -math.huge
    for _, x in ipairs(chord) do s_lo, s_hi = math.min(s_lo, x.start), math.max(s_hi, x.start) end
    if s_hi - s_lo <= M.TOL_S then return p end
    local lo, hi = math.huge, -math.huge
    local minEnd, maxEnd = math.huge, -math.huge
    local vmin, vmax = 127, 1
    for _, x in ipairs(chord) do
        lo, hi = math.min(lo, x.pitch), math.max(hi, x.pitch)
        minEnd, maxEnd = math.min(minEnd, x.stop), math.max(maxEnd, x.stop)
        vmin, vmax = math.min(vmin, x.vel), math.max(vmax, x.vel)
    end
    p.minVel, p.maxVel = vmin, vmax
    if #chord > 1 then
        p.moveNoteOff = maxEnd - minEnd > M.TOL_S
        local first_lo, first_hi = math.huge, math.huge
        for _, x in ipairs(chord) do
            if x.pitch == lo then first_lo = math.min(first_lo, x.start) end
            if x.pitch == hi then first_hi = math.min(first_hi, x.start) end
        end
        if first_hi < first_lo - M.TOL_S then p.upStrum = true
        elseif first_lo < first_hi - M.TOL_S then p.upStrum = false end
        local o = order(chord, p.upStrum)
        local gaps = {}
        for k = 1, #o - 1 do gaps[k] = math.abs(o[k + 1].start - o[k].start) * 1000 end
        table.sort(gaps)
        local m = #gaps
        p.offsetMs = (m % 2 == 1) and gaps[(m + 1) // 2] or (gaps[m // 2] + gaps[m // 2 + 1]) / 2
    end
    return M.normalize(p)
end

return M
