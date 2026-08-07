Output assets/demo.gif
Set Width 1200
Set Height 640
Set FontSize 15
Set Padding 20
Set Theme "Dracula"

# MineKG — 10-minute happy path, rendered offline (rule fallback, no API key).
# Runs against the sample exports in examples/exports.

Type "export MINEKG_HOME=/tmp/minekg_demo"
Enter
Sleep 500ms

Type "rm -rf /tmp/minekg_demo"
Enter
Sleep 300ms

Type "minekg ingest examples/exports"
Enter
Sleep 3s

Type "minekg link"
Enter
Sleep 3s

Type "minekg ask '去年和小张相关的所有资金往来合计'"
Enter
Sleep 3s
