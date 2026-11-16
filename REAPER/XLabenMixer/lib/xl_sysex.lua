-- X-Laben（PhoenixMAX）の SysEx を作る・読む。
-- 形：F0 00 40 04 01 58 12 [アドレス a1 a2 a3] [データ 1〜2 バイト] [チェックサム] F7
-- チェックサムはアドレスとデータの和から（Roland と同じ。0x12 は数えない）。
local M = {}

M.HEADER = {0x00, 0x40, 0x04, 0x01, 0x58, 0x12}

-- パートごとの EFX の割り当て（40 4x 22）の x。ch10 が 0、ch1〜9 が 1〜9、ch11〜16 が A〜F
M.PART_SUB = {[1] = 0x41, [2] = 0x42, [3] = 0x43, [4] = 0x44, [5] = 0x45, [6] = 0x46, [7] = 0x47, [8] = 0x48,
              [9] = 0x49, [10] = 0x40, [11] = 0x4A, [12] = 0x4B, [13] = 0x4C, [14] = 0x4D, [15] = 0x4E, [16] = 0x4F}
M.SUB_PART = {}
for ch, sub in pairs(M.PART_SUB) do M.SUB_PART[sub] = ch end
M.EFX_ASSIGN_OFFSET = 0x22

function M.checksum(bytes)
    local sum = 0
    for _, b in ipairs(bytes) do sum = sum + b end
    local c = 128 - (sum % 128)
    return c == 128 and 0 or c
end

-- バイトの表 → 文字列
function M.to_string(bytes)
    local parts = {}
    for i, b in ipairs(bytes) do parts[i] = string.char(b) end
    return table.concat(parts)
end

-- 文字列 → バイトの表
function M.to_bytes(msg)
    return {msg:byte(1, -1)}
end

-- X-Laben の SysEx の文字列（F0 … F7 込み）
function M.build(a1, a2, a3, data)
    local body = {a1, a2, a3}
    for _, d in ipairs(data) do body[#body + 1] = d end
    local out = {0xF0}
    for _, b in ipairs(M.HEADER) do out[#out + 1] = b end
    for _, b in ipairs(body) do out[#out + 1] = b end
    out[#out + 1] = M.checksum(body)
    out[#out + 1] = 0xF7
    return M.to_string(out)
end

-- 文字列（F0/F7 はあってもなくてもよい）→ {a1, a2, a3, data = {...}}。X-Laben の形でなければ nil
function M.parse(msg)
    local b = M.to_bytes(msg)
    local i = 1
    if b[1] == 0xF0 then i = 2 end
    for k = 1, #M.HEADER do
        if b[i + k - 1] ~= M.HEADER[k] then return nil end
    end
    local last = #b
    if b[last] == 0xF7 then last = last - 1 end
    local a = i + #M.HEADER
    -- アドレス 3 バイト、データ 1 バイト以上、チェックサム 1 バイト
    if last - a + 1 < 5 then return nil end
    local data = {}
    for k = a + 3, last - 1 do data[#data + 1] = b[k] end
    return {a1 = b[a], a2 = b[a + 1], a3 = b[a + 2], data = data}
end

-- 同じアドレスの値をデータだけ変えた文字列（元の長さ・形のまま、チェックサムを計算し直す）
function M.with_data(msg, data)
    local p = M.parse(msg)
    if not p then return nil end
    local new = {}
    for k = 1, #p.data do new[k] = data[k] ~= nil and data[k] or p.data[k] end
    return M.build(p.a1, p.a2, p.a3, new)
end

-- 音源全体をリセットするメッセージか（X-Laben / GS の 40 00 7F、システムモード 00 00 7F、GM システムオン）
function M.is_reset(msg)
    local p = M.parse(msg)
    if p then
        return (p.a1 == 0x40 or p.a1 == 0x00) and p.a2 == 0x00 and p.a3 == 0x7F
    end
    local b = M.to_bytes(msg)
    local i = b[1] == 0xF0 and 2 or 1
    if b[i] == 0x7E and b[i + 2] == 0x09 then return true end                      -- GM システムオン / オフ
    if b[i] == 0x41 and b[i + 2] == 0x42 and b[i + 3] == 0x12 and b[i + 4] == 0x40 and b[i + 5] == 0x00 and b[i + 6] == 0x7F then
        return true                                                                 -- Roland GS リセット
    end
    return false
end

-- 値を覚えておくときの鍵（同じ鍵の後のメッセージが前のものを上書きする）。X-Laben の形でなければ nil
function M.key(msg)
    local p = M.parse(msg)
    if not p then return nil end
    return string.format("%02X%02X%02X:%d", p.a1, p.a2, p.a3, #p.data)
end

-- パートの EFX の割り当てのメッセージなら (チャンネル, 値 0=オフ 1=A 2=B)
function M.efx_assign(msg)
    local p = M.parse(msg)
    if p and p.a1 == 0x40 and p.a3 == M.EFX_ASSIGN_OFFSET and M.SUB_PART[p.a2] then
        return M.SUB_PART[p.a2], p.data[1]
    end
    return nil
end

function M.efx_assign_msg(ch, value)
    return M.build(0x40, M.PART_SUB[ch], M.EFX_ASSIGN_OFFSET, {value})
end

return M
