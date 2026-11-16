-- プリセット（10 個）を JSON のファイルに置く。前の形（AutoStrummer_presets.lua）からの引っ越しも。
-- ファイルの形：{"version": 1, "slots": {"1": {...}, "2": {...}, ...}}（無い番号は書かない）
local M = {}
M.FIELDS = {"offsetMs", "minVel", "maxVel", "globalShift", "offShiftMs", "brushLenMs", "upStrum", "moveNoteOff", "brushing"}

-- ------------------------------------------------------------------ JSON（この形に要るだけ：オブジェクト・数・真偽・文字列）
local function enc_value(v)
    local t = type(v)
    if t == "boolean" then return v and "true" or "false" end
    if t == "number" then
        if v == math.floor(v) then return string.format("%d", v) end
        return string.format("%.6g", v)
    end
    if t == "string" then return '"' .. v:gsub('[%c"\\]', function(c) return string.format("\\u%04x", c:byte()) end) .. '"' end
    if t == "table" then
        local keys = {}
        for k in pairs(v) do keys[#keys + 1] = tostring(k) end
        table.sort(keys, function(a, b)
            local na, nb = tonumber(a), tonumber(b)
            if na and nb then return na < nb end
            return a < b
        end)
        local parts = {}
        for _, k in ipairs(keys) do
            local val = v[k]
            if val == nil then val = v[tonumber(k)] end
            parts[#parts + 1] = enc_value(k) .. ": " .. enc_value(val)
        end
        return "{" .. table.concat(parts, ", ") .. "}"
    end
    return "null"
end
M.encode = enc_value

-- JSON を読む。読めなければ nil と理由
function M.decode(s)
    local pos = 1
    local function ws() pos = s:find("[^ \t\r\n]", pos) or #s + 1 end
    local value
    local function str()
        local out = {}
        pos = pos + 1
        while true do
            local c = s:sub(pos, pos)
            if c == "" then error("文字列が閉じていない") end
            if c == '"' then pos = pos + 1 break end
            if c == "\\" then
                local e = s:sub(pos + 1, pos + 1)
                if e == "u" then
                    out[#out + 1] = utf8.char(tonumber(s:sub(pos + 2, pos + 5), 16))
                    pos = pos + 6
                else
                    out[#out + 1] = ({n = "\n", t = "\t", r = "\r", b = "\b", f = "\f"})[e] or e
                    pos = pos + 2
                end
            else
                out[#out + 1] = c
                pos = pos + 1
            end
        end
        return table.concat(out)
    end
    value = function()
        ws()
        local c = s:sub(pos, pos)
        if c == "{" then
            local obj = {}
            pos = pos + 1
            ws()
            if s:sub(pos, pos) == "}" then pos = pos + 1 return obj end
            while true do
                ws()
                if s:sub(pos, pos) ~= '"' then error("名前が無い") end
                local k = str()
                ws()
                if s:sub(pos, pos) ~= ":" then error(": が無い") end
                pos = pos + 1
                obj[k] = value()
                ws()
                local d = s:sub(pos, pos)
                pos = pos + 1
                if d == "}" then return obj end
                if d ~= "," then error(", か } が無い") end
            end
        elseif c == "[" then
            local arr = {}
            pos = pos + 1
            ws()
            if s:sub(pos, pos) == "]" then pos = pos + 1 return arr end
            while true do
                arr[#arr + 1] = value()
                ws()
                local d = s:sub(pos, pos)
                pos = pos + 1
                if d == "]" then return arr end
                if d ~= "," then error(", か ] が無い") end
            end
        elseif c == '"' then
            return str()
        elseif s:sub(pos, pos + 3) == "true" then pos = pos + 4 return true
        elseif s:sub(pos, pos + 4) == "false" then pos = pos + 5 return false
        elseif s:sub(pos, pos + 3) == "null" then pos = pos + 4 return nil
        else
            local num = s:match("^-?%d+%.?%d*[eE]?[-+]?%d*", pos)
            if not num or num == "" then error("読めない文字：" .. c) end
            pos = pos + #num
            return tonumber(num)
        end
    end
    local ok, v = pcall(function()
        local x = value()
        ws()
        if pos <= #s then error("後ろに余分な文字がある") end
        return x
    end)
    if not ok then return nil, tostring(v) end
    return v
end

-- ------------------------------------------------------------------ プリセット
-- 1 つのプリセットを今の形にする（前の形：endShiftMs を brushingMode のときは長さ、そうでなければ Note Off Shift に）
function M.upgrade(p)
    if type(p) ~= "table" then return nil end
    local out = {}
    for _, k in ipairs(M.FIELDS) do out[k] = p[k] end
    if p.brushingMode ~= nil and out.brushing == nil then out.brushing = p.brushingMode and true or false end
    if p.endShiftMs ~= nil then
        if out.brushing then
            if out.brushLenMs == nil then out.brushLenMs = p.endShiftMs end
        elseif out.offShiftMs == nil then
            out.offShiftMs = p.endShiftMs
        end
    end
    return out
end

-- 前の形（Lua の表を返すファイル）を読む。コードとしては実行しない（中身が表の形のときだけ読む）
function M.read_old(text)
    if not text or not text:match("^%s*return%s*{") then return nil end
    local f = load(text, "=old_presets", "t", {})   -- 何も使えない環境で読む
    if not f then return nil end
    local ok, t = pcall(f)
    if not ok or type(t) ~= "table" then return nil end
    local slots = {}
    for i = 1, 10 do slots[i] = M.upgrade(t[i]) end
    return slots
end

-- JSON の中身から {[1..10] = プリセット or nil}
function M.from_json(text)
    local v = M.decode(text or "")
    local slots = {}
    if type(v) == "table" and type(v.slots) == "table" then
        for k, p in pairs(v.slots) do
            local i = tonumber(k)
            if i and i >= 1 and i <= 10 then slots[i] = M.upgrade(p) end
        end
    end
    return slots
end

function M.to_json(slots)
    local s = {}
    for i = 1, 10 do if slots[i] then s[tostring(i)] = slots[i] end end
    return M.encode({version = 1, slots = s}) .. "\n"
end

return M
