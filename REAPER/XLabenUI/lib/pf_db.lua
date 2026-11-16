-- 音色の表（data/tones_db.lua）を引く。REAPER を使わない。
--   is_drum：ドラムのキットか、lsb：マップ（1 = SC-55、2 = SC-88、3 = SC-88Pro、4 = X-Laben）、msb：バンク、pc：プログラム（0〜127）
local M = {}

M.MAPS = {
    {lsb = 1, name = "1: SC-55 Map"},
    {lsb = 2, name = "2: SC-88 Map"},
    {lsb = 3, name = "3: SC-88Pro Map"},
    {lsb = 4, name = "4: XING Map"},
}

-- tones（{name, msb, lsb, pc, is_drum, ...} の並び）から引く表を作る
function M.new(tones)
    local db = {tones = tones, at = {[false] = {}, [true] = {}}, by_pc = {[false] = {}, [true] = {}}}
    for _, t in ipairs(tones) do
        local d = t.is_drum and true or false
        db.at[d][t.lsb] = db.at[d][t.lsb] or {}
        db.at[d][t.lsb][t.msb] = db.at[d][t.lsb][t.msb] or {}
        db.at[d][t.lsb][t.msb][t.pc] = t
        db.by_pc[d][t.pc] = db.by_pc[d][t.pc] or {}
        table.insert(db.by_pc[d][t.pc], t)
    end
    return db
end

-- ちょうどその番号の音色（無ければ nil）
function M.get(db, is_drum, lsb, msb, pc)
    local a = db.at[is_drum and true or false][lsb]
    return a and a[msb] and a[msb][pc] or nil
end

-- そのマップ・バンクに音色が 1 つでもあるか
function M.has_msb(db, is_drum, lsb, msb)
    local a = db.at[is_drum and true or false][lsb]
    return a ~= nil and a[msb] ~= nil
end

-- プログラム pc のバリエーション（バンク違い）。lsb が nil なら全部のマップ。バンクの大きい順（同じなら マップの大きい順）
function M.variations(db, is_drum, pc, lsb)
    local out = {}
    for _, t in ipairs(db.by_pc[is_drum and true or false][pc] or {}) do
        if lsb == nil or t.lsb == lsb then out[#out + 1] = t end
    end
    table.sort(out, function(a, b)
        if a.msb ~= b.msb then return a.msb > b.msb end
        return a.lsb > b.lsb
    end)
    return out
end

-- 名前（表示用）。ちょうどの音色が無ければ、GS と同じく同じマップのバンク 0（キャピタル）、それも無ければほかのマップで探す。
-- (名前, ちょうどか)。何も無ければ nil
function M.name_for(db, is_drum, lsb, msb, pc)
    local t = M.get(db, is_drum, lsb, msb, pc)
    if t then return t.name, true end
    t = M.get(db, is_drum, lsb, 0, pc)
    if t then return t.name, false end
    for l = 4, 1, -1 do
        t = M.get(db, is_drum, l, msb, pc) or M.get(db, is_drum, l, 0, pc)
        if t then return t.name, false end
    end
    return nil
end

-- 名前で探す（大文字・小文字は区別しない。空白で区切った言葉が全部含まれるもの）。プログラム・バンク・マップの順
function M.search(db, is_drum, query)
    local words = {}
    for w in (query or ""):lower():gmatch("%S+") do words[#words + 1] = w end
    if #words == 0 then return {} end
    local out = {}
    for _, t in ipairs(db.tones) do
        if (t.is_drum and true or false) == (is_drum and true or false) then
            local name = t.name:lower()
            local ok = true
            for _, w in ipairs(words) do
                if not name:find(w, 1, true) then ok = false break end
            end
            if ok then out[#out + 1] = t end
        end
    end
    table.sort(out, function(a, b)
        if a.pc ~= b.pc then return a.pc < b.pc end
        if a.msb ~= b.msb then return a.msb < b.msb end
        return a.lsb < b.lsb
    end)
    return out
end

-- Cakewalk のバンク番号（MSB × 128 + LSB）
function M.cw_bank(msb, lsb) return msb * 128 + lsb end

return M
