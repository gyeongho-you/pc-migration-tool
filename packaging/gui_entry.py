"""pcmigrate-gui.exe 진입점(PyInstaller 용). 창 모드이므로 콘솔 출력은 없다(--version/--help 는 메시지 상자로 알린다)."""
import sys

from pcmigrate.gui_args import handle_gui_args

if handle_gui_args(sys.argv[1:]):
    raise SystemExit(0)

from pcmigrate.gui.app import main

raise SystemExit(main())
