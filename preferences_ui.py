"""Small native preferences dialog with reversible display preview."""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialog, QWidget, QTabWidget, QComboBox, QSlider, QLineEdit, QScrollArea
from display_settings import DisplayPreferences, WINDOWS
from ui_helpers import box, label, button
from framework import api
from theme import APP_NAME, TAGLINE


class PreferencesDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner; self.display = owner.display
        self.original = self.display.current; self.geometry = owner.saveGeometry()
        self.display.previewing = True; self.committed = False
        self.setWindowTitle('偏好设置')
        available=self.screen().availableGeometry(); self.resize(min(500,available.width()),min(550,available.height()-40))
        layout = box(self, margins=(22,20,22,20), spacing=14)
        tabs = QTabWidget(); layout.addWidget(tabs,1)
        page = QWidget(); body = box(page, spacing=14); self.add_page(tabs,page,'显示')
        body.addWidget(label('调到舒服的大小','h2'))
        body.addWidget(label('窗口布局随大小自动调整；系统高分屏缩放继续生效。','muted',True))
        body.addWidget(label('窗口大小','section'))
        self.window_mode = QComboBox(); self.window_mode.setAccessibleName('窗口大小')
        for value, title in WINDOWS: self.window_mode.addItem(title,value)
        self.window_mode.setCurrentIndex(self.window_mode.findData(self.original.window_mode)); body.addWidget(self.window_mode)
        heading = box(horizontal=True); heading.addWidget(label('字体大小','section')); heading.addStretch()
        self.size_label = label(''); heading.addWidget(self.size_label); body.addLayout(heading)
        self.font_size = QSlider(Qt.Horizontal); self.font_size.setRange(12,18); self.font_size.setValue(self.original.font_size)
        self.font_size.setAccessibleName('字体大小'); self.font_size.setTickPosition(QSlider.TicksBelow); self.font_size.setTickInterval(1)
        body.addWidget(self.font_size)
        self.preview = label(APP_NAME+'，'+TAGLINE+'\n任务与输入文字一起调整。','h2',True); body.addWidget(self.preview)
        body.addWidget(label('调整后立即预览；保存后下次启动沿用。取消会恢复原来的字体和窗口。','muted',True))
        body.addStretch(); body.addWidget(button('恢复默认显示','outline',callback=self.reset_display))
        page = QWidget(); body = box(page,spacing=14); self.add_page(tabs,page,'常规')
        self.duration = QComboBox()
        if owner.focus: body.addWidget(label('一段专注时间','section'))
        for minutes in (15,25,45,60): self.duration.addItem(str(minutes)+' 分钟',minutes)
        self.duration.setCurrentIndex(self.duration.findData(owner.focus_duration//60)); body.addWidget(self.duration); self.duration.setVisible(bool(owner.focus))
        body.addWidget(button('应用更新','outline',callback=owner.open_updates))
        body.addWidget(button('本地存储与留存','outline',callback=self.storage))
        location = QLineEdit(str(owner.tasks.path)); location.setReadOnly(True)
        body.addWidget(label('本地数据文件','section')); body.addWidget(location)
        body.addWidget(label('服务框架：YomkServerPy '+api.version(),'muted'))
        body.addWidget(label('Ctrl N 新建 · Ctrl K 搜索'+('\nCtrl Shift F 开始 / 暂停专注' if owner.focus else ''),'muted',True)); body.addStretch()
        self.notice = label('','muted',True); layout.addWidget(self.notice)
        actions = box(horizontal=True); actions.addStretch()
        actions.addWidget(button('取消','outline',callback=self.reject)); actions.addWidget(button('保存设置','primary',callback=self.accept)); layout.addLayout(actions)
        self.font_size.valueChanged.connect(self.preview_font)
        self.window_mode.currentIndexChanged.connect(self.preview_window)
        self.size_label.setText(f'{self.font_size.value()} px')

    def storage(self):
        from storage_ui import StorageDialog
        dialog=StorageDialog(self.owner); self.owner.storage_dialog=dialog
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.finished.connect(lambda: setattr(self.owner,"storage_dialog",None))
        dialog.show()

    @staticmethod
    def add_page(tabs, page, title):
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(page)
        scroll.setMinimumHeight(200); tabs.addTab(scroll,title)

    def values(self):
        return DisplayPreferences(self.font_size.value(), self.window_mode.currentData())

    def preview_font(self):
        self.size_label.setText(f'{self.font_size.value()} px')
        self.display.apply(self.values())

    def preview_window(self):
        self.display.fit_window(self.owner,self.window_mode.currentData())

    def reset_display(self):
        self.font_size.setValue(13); self.window_mode.setCurrentIndex(0)
        self.preview_window()

    def accept(self):
        try:
            minutes = self.duration.currentData()
            if self.owner.focus and minutes*60 != self.owner.focus_duration:
                self.owner.focus.set_duration(minutes); self.owner.refresh_timer()
            self.display.save(self.values())
        except (ValueError,OSError) as exc:
            self.notice.setText(str(exc)); return
        self.committed = True; self.display.previewing = False
        self.display.remember_window(self.owner)
        super().accept()

    def done(self, code):
        if not self.committed:
            self.display.apply(self.original); self.owner.restoreGeometry(self.geometry)
            self.display.previewing = False
        super().done(code)
