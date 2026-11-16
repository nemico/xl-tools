X-Laben ツール（仲間内で使う。外に出さない）

■ 入っているもの
  ・ミキサー（REAPER\XLabenMixer\XLaben_Mixer.lua）
  ・再生・停止（REAPER\XLabenMixer\XLaben_Play.lua。Space に割り当てる。送り終わって音源が落ち着いてから再生する）
  ・EFX（REAPER\EFXUI\xlaben_efx_controller.lua）
  ・ドラム（REAPER\DrumReapermacro\XLaben_Drum.lua）
  ・AI でプロジェクトを作る（A2M\scripts\ai_build_project.lua。テンポマップ・ステム・ドラム/ベース/歌の下書き・コード）
  ・インストール・更新（XLaben_Update.lua）

■ 入れ方（最初に1回）
  1. REAPER で「アクション → アクションリストを表示 → 新規アクション → ReaScript を読み込む」で XLaben_Update.lua を選ぶ。画面が出る。
  2. 「インストールする」を押す。
     → REAPER の Scripts フォルダの中の XLabenTools フォルダに全部入り、アクションに登録される。
     別の場所に入れたいときは、押す前に「場所を変える」で選ぶ（選んだフォルダの中に XLabenTools フォルダができる）。
  3. アクションリストで「XLaben」を探して、ショートカットを割り当てる。再生・停止（XLaben_Play.lua）は Space に。
  4. REAPER の設定：オプション → 設定 → オーディオ →「停止中も FX を動かす（Run FX when stopped）」をオン。
  渡された XLaben_Update.lua は、入れ終わったら消してよい（同じものが XLabenTools フォルダに入る）。

■ 前に渡したマクロ（EFXUI・ドラム・ミキサー）を使っていた人
  ・前のマクロのファイルと登録は、そのまま残る（消えない。今までどおり動く）。
  ・前の EFXUI で保存したプリセットは、入れるときに新しいほうへ引き継がれる。
  ・画面に「前の版のマクロが…登録されたままです」と出る。「前の版の登録を外す」を押すと、前のマクロの登録だけ外れる
    （ファイルは消えない）。そのあと、新しいアクションにショートカットを割り当てる。
    外さないと、アクションリストに同じ名前が 2 つ並び、ショートカットは前のマクロを開く。

■ AI の下書きも使う人
  ・上の入れ方のあと、全部入りの ZIP（Python とモデル入り。約 6GB）の中の XLabenTools フォルダの中身を、
    入れた場所の XLabenTools フォルダに上書きで入れる（画面の「フォルダを開く」で開ける）。
  ・AI の下書きは、英数字だけのパスでないと動かない。画面に注意が出たら「場所を変える」で英数字だけの場所へ移す（例：D:\Tools）。

■ 更新
  ・アクション「XLaben_Update.lua」を実行して「更新する」を押す。新しい版があれば入れ替わる。
  ・更新したら、画面に出る「やること」（新しいアクションへのショートカット、設定など）をして、REAPER を一度閉じて開き直す。
  ・今の版は VERSION.txt、変わった点は CHANGES.txt。
  ・更新で変わらないもの：user フォルダ、ログ、自分で保存した EFX のプリセット、A2M の python・models。
  ・「AI の下書きの土台が古い」と出たら、新しい全部入りの ZIP をもらって、同じフォルダに上書きで入れる。

■ 場所を変える
  ・画面の「場所を変える」で、フォルダごと別の場所へ移せる。アクションは新しい場所で登録し直される。
  ・ショートカットは割り当て直しになる（REAPER は場所ごとに別のアクションとして覚えるため）。場所は、入れる前に決めるのがよい。

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
  ・版（VERSION.txt の中身）と、次のファイルを渡す（XLabenTools フォルダの中）：
    更新：user\update_log.txt　　下書き：A2M\scratch\reaper_job\log.txt　　再生：REAPER\XLabenMixer\logs\play_log.txt
