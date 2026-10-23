"""pcmigrate-gui.exe 진입점(PyInstaller 용). 창 모드이므로 콘솔 출력은 없다."""
from pcmigrate.gui.app import main

raise SystemExit(main())
