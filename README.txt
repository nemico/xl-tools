X-Laben ツール（仲間内で使う。外に出さない）

■ 入っているもの
  ・ミキサー（REAPER\XLabenMixer\XLaben_Mixer.lua）
  ・再生・停止（REAPER\XLabenMixer\XLaben_Play.lua。Space に割り当てる。送り終わって音源が落ち着いてから再生する）
  ・EFX（REAPER\EFXUI\xlaben_efx_controller.lua）
  ・ドラム（REAPER\DrumReapermacro\XLaben_Drum.lua）
  ・AI でプロジェクトを作る（A2M\scripts\ai_build_project.lua。テンポマップ・ステム・ドラム/ベース/歌の下書き・コード）
  ・インストール・更新（XLaben_Update.lua）

■ 入れ方（最初に1回）
  A. ツールだけ使う人（ミキサー・EFX・ドラム・再生）
    1. 英数字だけのパスに空のフォルダを作る（例：D:\XLabenTools）。日本語や空白の入った場所は避ける。
    2. そこに XLaben_Update.lua を置く。
    3. REAPER で「アクション → アクションリストを表示 → 新規アクション → ReaScript を読み込む」で XLaben_Update.lua を選び、実行する。
       → 最新の版がそのフォルダに入り、アクションに登録される。
  B. AI の下書きも使う人
    1. 全部入りの ZIP（Python とモデル入り。約 6GB）を、英数字だけのパスに展開する（例：D:\XLabenTools）。
    2. その中の XLaben_Update.lua を、A の 3 と同じように実行する（最新の版に更新され、アクションに登録される）。
  どちらも、そのあと：
    ・アクションリストでショートカットを割り当てる。再生・停止（XLaben_Play.lua）は Space に。
    ・REAPER の設定：オプション → 設定 → オーディオ →「停止中も FX を動かす（Run FX when stopped）」をオン。

■ 更新
  ・アクション「XLaben_Update.lua」を実行する。新しい版があれば入れ替わる。無ければ「最新です」と出る。
  ・更新したら、REAPER を一度閉じて開き直す。
  ・今の版は VERSION.txt、変わった点は CHANGES.txt。
  ・更新で変わらないもの：user フォルダ、ログ、自分で保存した EFX のプリセット、A2M の python・models。
  ・フォルダを動かしたら、もう一度 XLaben_Update.lua を実行する（登録はファイルの場所で覚えるため）。
  ・「AI の下書きの土台が古い」と出たら、新しい全部入りの ZIP をもらって、同じフォルダに上書きで展開する。

■ 使うときの決まり
  ・プロジェクトはテンプレート（260603x）のトラック名のまま使う（A01〜A16、X-Laben、A01 SYSTEM_EXCLUSIVE）。
  ・初めてミキサー・EFX・ドラム・再生を使うと、X-Laben トラックの先頭に JSFX（A2M Live MIDI preview）が自動で入る。
  ・ミキサー・EFX・ドラムは、自分のショートカットで開く・閉じる。ドッキングの裏に隠れているときは前に出る。

■ AI の下書きに要るもの
  ・Windows、NVIDIA の GPU（RTX 20 / GTX 16 以降。GPU のメモリは 4GB 以上あれば足りる。測った最大は約 2.7GB）。
    GTX 10 以前・GPU が無い PC では CPU で動く（かなり遅い）。
  ・Python やモデルは全部入りの ZIP に入っている。下書きはネットにつながない。インストールは要らない。
  ・原曲の音声アイテムを1つ選んで「AI でプロジェクトを作る」を実行する。数分かかる。その間も REAPER は使える。

■ うまくいかないとき
  ・版（VERSION.txt の中身）と、次のファイルを渡す：
    更新：user\update_log.txt　　下書き：A2M\scratch\reaper_job\log.txt　　再生：REAPER\XLabenMixer\logs\play_log.txt
