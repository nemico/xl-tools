-- @description X-Laben：音源に入った MIDI の記録を書き出す（調べる用）
-- @version 1.0.0
--
-- X-Laben トラックの JSFX（A2M Live MIDI preview）が、音源（PhoenixMAX）に入れた・止めたノート以外のメッセージを
-- 記録している。その最後の 600 件を、このスクリプトと同じフォルダの logs/midi_log.txt に書く。
-- 使い方：再生 → 一時停止（音が大きくなる所）→ このスクリプトを実行。

local r = reaper
local DIR = debug.getinfo(1, "S").source:match("^@?(.*[\\/])")
local LOG, N, KEEP = 400000, 4096, 600
local SRC = {[1] = "トラック", [2] = "スクリプト", [3] = "止めた"}

r.gmem_attach("A2M_LIVE")
local total = r.gmem_read(LOG)
local first = math.max(0, total - KEEP, total - N)
local lines = {string.format("記録 %d 件のうち、最後の %d 件（ノートは記録しない）。JSFX の版 %d、ふつうの CC 以外の数 %d",
                             total, total - first, r.gmem_read(4), r.gmem_read(6)),
               "番号\t出どころ\t再生中\t位置(秒)\tバイト"}
local function name(b0, b1, b2)
    local kind, ch = b0 & 0xF0, (b0 & 0x0F) + 1
    if b0 == 0xF0 then return "SysEx" end
    if kind == 0xB0 then return string.format("ch%d CC%d=%d", ch, b1, b2) end
    if kind == 0xC0 then return string.format("ch%d PC %d", ch, b1) end
    if kind == 0xE0 then return string.format("ch%d ピッチベンド %d", ch, b1 + b2 * 128) end
    if kind == 0xD0 then return string.format("ch%d チャンネルプレッシャー %d", ch, b1) end
    if kind == 0xA0 then return string.format("ch%d ポリプレッシャー", ch) end
    return string.format("%02X", b0)
end
for k = first, total - 1 do
    local e = LOG + 1 + (k % N) * 8
    local src, ps, pos, len = r.gmem_read(e), r.gmem_read(e + 1), r.gmem_read(e + 2), r.gmem_read(e + 3)
    local b = {}
    for i = 0, 3 do b[i] = r.gmem_read(e + 4 + i) end
    local hex = {}
    for i = 0, math.min(len, 4) - 1 do hex[#hex + 1] = string.format("%02X", b[i]) end
    lines[#lines + 1] = string.format("%d\t%s\t%s\t%.3f\t%s%s\t%s", k, SRC[src] or tostring(src), (ps % 2 == 1) and "再生" or "停止",
                                      pos, table.concat(hex, " "), len > 4 and (" …(" .. len .. ")") or "", name(b[0], b[1], b[2]))
end
r.RecursiveCreateDirectory(DIR .. "logs", 0)
local path = DIR .. "logs/midi_log.txt"
local f = io.open(path, "wb")
if f then
    f:write(table.concat(lines, "\n") .. "\n")
    f:close()
    r.ShowMessageBox(string.format("%d 件を書き出しました：\n%s", total - first, path), "X-Laben MIDI の記録", 0)
else
    r.ShowMessageBox("書き出せませんでした：" .. path, "X-Laben MIDI の記録", 0)
end
