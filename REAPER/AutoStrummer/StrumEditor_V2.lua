-- @description X-Laben オートストラマー（前の名前 StrumEditor_V2 のまま、新しい XLaben_Strum.lua を開く）
-- 前にこのスクリプトをアクションに登録してあれば、そのまま新しい画面が開く。前の中身は old/StrumEditor_V2.lua（使わない）。
local DIR = debug.getinfo(1, "S").source:match("^@?(.*[\\/])")
dofile(DIR .. "XLaben_Strum.lua")
