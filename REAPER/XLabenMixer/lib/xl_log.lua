-- 調べる用の記録：logs/play_log.txt に1行ずつ足す（Space がどう動いたか、XLaben_Sync が何を送ったか）。
-- 大きくなったら（M.MAX_BYTES）、古い半分を捨てる。テストでは XL_LOG_PATH = false で書かない
local r = reaper
local DIR = debug.getinfo(1, "S").source:match("^@?(.*[\\/])")

local M = {}
M.MAX_BYTES = 300000

function M.path()
    if XL_LOG_PATH ~= nil then return XL_LOG_PATH end
    return DIR:gsub("lib[\\/]$", "") .. "logs/play_log.txt"
end

local function trim(path)
    local f = io.open(path, "rb")
    if not f then return end
    local t = f:read("*a")
    f:close()
    if #t <= M.MAX_BYTES then return end
    local keep = t:sub(#t - M.MAX_BYTES // 2)
    keep = keep:sub((keep:find("\n") or 0) + 1)
    f = io.open(path, "wb")
    if f then f:write(keep); f:close() end
end

-- who：「Space」「Sync」など。fmt, ...：string.format と同じ
function M.write(who, fmt, ...)
    local path = M.path()
    if not path then return end
    local ok, line = pcall(string.format, fmt, ...)
    if not ok then line = tostring(fmt) end
    local now = r.time_precise()
    local f = io.open(path, "ab")
    if not f then
        r.RecursiveCreateDirectory(path:match("^(.*)[\\/]") or ".", 0)
        f = io.open(path, "ab")
        if not f then return end
    end
    f:write(string.format("%s %10.3f %-5s %s\n", os.date("%H:%M:%S"), now, who, line))
    f:close()
    M.count = (M.count or 0) + 1
    if M.count % 200 == 0 then trim(path) end
end

return M
