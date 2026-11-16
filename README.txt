X-Laben ツール（仲間内で使う。外に出さない）

■ 入っているもの
  ・ミキサー（REAPER\XLabenMixer\XLaben_Mixer.lua）
  ・再生・停止（REAPER\XLabenMixer\XLaben_Play.lua。Space に割り当てる。送り終わって音源が落ち着いてから再生する）
  ・EFX（REAPER\EFXUI\xlaben_efx_controller.lua）
  ・ドラム（REAPER\DrumReapermacro\XLaben_Drum.lua）
  ・オートストラマー（REAPER\AutoStrummer\StrumEditor_V2.lua）
  ・AI でプロジェクトを作る（A2M\scripts\ai_build_project.lua。テンポマップ・ステム・ドラム/ベース/歌の下書き・コード）
  ・納品チェック（A2M\scripts\delivery_check.lua。R02Checker で確かめて、引っかかった所にマーカーを置く）
  ・インストール・更新（XLaben_Update.lua）
  AI の下書きと納品チェックは、データの ZIP（Python・モデル・MIDIChecker。GitHub に置けない大きいもの）が要る。

■ 入れ方（最初に1回）
  1. REAPER で「アクション → アクションリストを表示 → 新規アクション → ReaScript を読み込む」で XLaben_Update.lua を選ぶ。画面が出る。
  2. 「インストールする」を押す。
     → REAPER の Scripts フォルダの中の XLabenTools フォルダに全部入り、アクションに登録される。
     別の場所に入れたいときは、押す前に「場所を変える」で選ぶ（選んだフォルダの中に XLabenTools フォルダができる）。
  3. 新しく使うアクションにショートカットを割り当てる（アクションリストで「XLaben」を探す）。再生・停止（XLaben_Play.lua）は Space に。
  4. REAPER の設定：オプション → 設定 → オーディオ →「停止中も FX を動かす（Run FX when stopped）」をオン。
  5. REAPER を閉じて、開き直す。

■ 前に渡したマクロ（EFXUI・ドラム・ミキサー・オートストラマー）を使っていた人
  ・入れると、前のマクロは新しいものに置き換わる。割り当てていたショートカットとツールバーのボタンは、そのまま新しいほうで動く。
  ・置き換わるのは REAPER を閉じたとき（REAPER が開いている間は、登録を書き換えられないため）。開き直すと新しいほうが動く。
  ・前のファイルは XLabenTools\old_macros\<日付> に移る（消えない）。REAPER の登録の写し（reaper-kb.ini.bak）もそこに残る。
  ・前の EFXUI とオートストラマーで保存したプリセットは、新しいほうへ引き継がれる。

■ AI の下書き・納品チェックも使う人（データの ZIP）
  ・データの ZIP（XLabenTools_data_….zip。約 6GB）を、展開せずにそのまま XLabenTools フォルダに置く（画面の「フォルダを開く」で開ける）。
  ・アクション「XLaben_Update.lua」を実行する。ZIP が自動で入って、ZIP は消える（数分。その間 REAPER は止まって見える）。
  ・まだ何も入れていない人は、渡された XLaben_Update.lua と同じフォルダに ZIP を置いて実行し、「ZIP からインストールする」を押してもよい。
  ・AI の下書きは、英数字だけのパスでないと動かない。画面に注意が出たら「場所を変える」で英数字だけの場所へ移す（例：D:\Tools）。

■ 更新
  ・アクション「XLaben_Update.lua」を実行して「更新する」を押す。新しい版があれば入れ替わる。
  ・更新したら、画面に出る「やること」（新しいアクションへのショートカット、設定など）をして、REAPER を一度閉じて開き直す。
  ・今の版は VERSION.txt、変わった点は CHANGES.txt。
  ・更新で変わらないもの：user フォルダ、ログ、自分で保存したプリセット、A2M の python・models・MIDIChecker。
  ・「土台が古い」と出たら、新しいデータの ZIP をもらって、XLabenTools フォルダに置いて実行する。
  ・データの ZIP はめったに変わらない。ふだんの更新は「更新する」だけでよい（ZIP は要らない）。

■ 場所を変える
  ・画面の「場所を変える」で、フォルダごと別の場所へ移せる。ショートカットは、REAPER を開き直すとそのまま使える。

■ 使うときの決まり
  ・プロジェクトはテンプレート（260603x）のトラック名のまま使う（A01〜A16、X-Laben、A01 SYSTEM_EXCLUSIVE）。
  ・初めてミキサー・EFX・ドラム・再生を使うと、X-Laben トラックの先頭に JSFX（A2M Live MIDI preview）が自動で入る。
  ・ミキサー・EFX・ドラムは、自分のショートカットで開く・閉じる。ドッキングの裏に隠れているときは前に出る。

■ AI の下書きに要るもの
  ・Windows、NVIDIA の GPU（RTX 20 / GTX 16 以降。GPU のメモリは 4GB 以上あれば足りる。測った最大は約 2.7GB）。
    GTX 10 以前・GPU が無い PC では CPU で動く（かなり遅い）。
  ・原曲の音声アイテムを1つ選んで「AI でプロジェクトを作る」を実行する。数分かかる。その間も REAPER は使える。

■ うまくいかないとき
  ・版（VERSION.txt の中身）と、次のファイルを渡す（XLabenTools フォルダの中）：
    更新：user\update_log.txt　　下書き：A2M\scratch\reaper_job\log.txt　　再生：REAPER\XLabenMixer\logs\play_log.txt
