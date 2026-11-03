# -*- coding: utf-8 -*-
"""与 DayNest 相同的 Qt 半透明窗口、布局与白弧缩放握柄。"""
import os, sys, json, math, time, types, threading, traceback
from pathlib import Path
from PySide6.QtCore import Qt,QPoint,QRectF,QTimer,QEvent,QObject,Signal,QPropertyAnimation,QEasingCurve
from PySide6.QtGui import QColor,QPainter,QPainterPath,QPen,QRegion,QFont,QCursor
from PySide6.QtWidgets import QApplication,QWidget,QLabel,QVBoxLayout,QHBoxLayout,QSizeGrip,QToolButton,QSlider,QComboBox,QMenu,QAbstractButton,QSizePolicy,QScrollArea,QFrame,QSpinBox,QCheckBox,QGridLayout,QProgressBar,QScrollBar
from hud_geometry import SHAPES,shape_path,content_rect,grip_position,DockManager

HERE=Path(__file__).resolve().parent
CONFIG=Path(os.environ.get('HUD_QT_CONFIG',str(HERE/'hud-qt.json')))
TEST=bool(os.environ.get('HUD_QT_TEST'))
print('HUD start',time.strftime('%Y-%m-%d %H:%M:%S'),'test='+str(TEST))
def default_profiles():
    return {'rail':{'shape':'圆角','width':68,'height':252,'x':80,'y':350,'dock_edge':None},
            'details':{'shape':'圆角','width':312,'height':360,'x':160,'y':300,'dock_edge':None,'follow':True},
            'settings':{'shape':'圆角','width':370,'height':490,'x':500,'y':300,'dock_edge':None}}
DEFAULT={'theme':'dark','transparency':40,'pinned':False,'locked':False,'layout':'separate'}
def load_config():
    cfg=dict(DEFAULT);cfg['windows']=default_profiles()
    try:
        old=json.loads(CONFIG.read_text(encoding='utf-8'))
        if not isinstance(old,dict):return cfg
        if old.get('theme') in ('light','dark'):cfg['theme']=old['theme']
        if isinstance(old.get('transparency'),(int,float)):cfg['transparency']=max(0,min(85,round(old['transparency'])))
        for key in ('pinned','locked'):
            if isinstance(old.get(key),bool):cfg[key]=old[key]
        if isinstance(old.get('windows'),dict):
            for role,profile in cfg['windows'].items():
                previous=old['windows'].get(role,{})
                if not isinstance(previous,dict):continue
                if previous.get('shape') in SHAPES:profile['shape']=previous['shape']
                for key,lo,hi in [('width',52,1600),('height',100,1400),('x',-20000,20000),('y',-20000,20000)]:
                    value=previous.get(key)
                    if isinstance(value,(int,float)) and math.isfinite(value):profile[key]=max(lo,min(hi,round(value)))
                for key in ('dock_edge','dock_screen','dock_offset','follow'):
                    if key in previous:profile[key]=previous[key]
        else:
            for key in ('x','y'):
                if isinstance(old.get(key),(int,float)):cfg['windows']['rail'][key]=round(old[key])
            for key in ('width','height'):
                if isinstance(old.get(key),(int,float)):cfg['windows']['details'][key]=max(240 if key=='width' else 200,round(old[key]))
            if old.get('shape') in SHAPES:
                cfg['windows']['rail']['shape']=old['shape'];cfg['windows']['details']['shape']=old['shape']
    except (OSError,ValueError,TypeError):pass
    return cfg

def load_core():
    path=HERE/'ai-hud.py'
    source=path.read_text(encoding='utf-8').split('# ---------------------------------------------------------------- 渲染')[0]
    source='\n'.join(line for line in source.splitlines() if not line.startswith('from PIL import'))
    core=types.ModuleType('hud_data');core.__file__=str(path)
    exec(compile(source,str(path),'exec'),core.__dict__)
    return core

class ThemeReveal(QWidget):
    def __init__(self,parent,old,origin,started):
        super().__init__(parent);self.old=old;self.center=parent.mapFromGlobal(origin);self.started=started;self.radius=0
        self.end=max(math.hypot(self.center.x()-x,self.center.y()-y) for x,y in ((0,0),(parent.width(),0),(0,parent.height()),(parent.width(),parent.height())))+4
        self.setGeometry(parent.rect());self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.show();self.raise_();self.timer=QTimer(self);self.timer.timeout.connect(self.tick);self.timer.start(16)
    def tick(self):
        self.radius=(time.monotonic()-self.started)*1800
        if self.radius>=self.end:self.timer.stop();self.deleteLater()
        else:self.update()
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path=QPainterPath();path.addRect(QRectF(self.rect()));path.addEllipse(self.center,self.radius,self.radius)
        path.setFillRule(Qt.FillRule.OddEvenFill);p.setClipPath(path);p.drawPixmap(0,0,self.old)

class WhiteGrip(QSizeGrip):
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w,h=self.width()-3,self.height()-3
        for color,width in ((QColor(48,63,73,145),4.),(QColor('white'),2.2)):
            pen=QPen(color,width);pen.setCapStyle(Qt.PenCapStyle.RoundCap);p.setPen(pen)
            p.drawArc(QRectF(-w+1,-h+1,2*w,2*h),270*16,90*16)
    def mousePressEvent(self,event):
        if self.window().locked:event.ignore();return
        self.window().dock.animation.stop();self.window().resizing=True;super().mousePressEvent(event)
    def mouseReleaseEvent(self,event):
        super().mouseReleaseEvent(event);w=self.window();w.resizing=False;w.dock.ensure_reachable();w.persist()

class GlassWindow(QWidget):
    def __init__(self,c,title,minimum,role='settings'):
        super().__init__(None,Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint|Qt.WindowType.Window)
        self.controller=c;self.role=role;self.profile=c.cfg['windows'][role];self.resizing=False;self._square_adjusting=False
        self.setWindowTitle(title);self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground,True)
        self.content=QWidget()
        self.scroll=None
        if role!='rail':
            self.scroll=QScrollArea(self);self.scroll.setFrameShape(QFrame.Shape.NoFrame);self.scroll.setWidgetResizable(True)
            self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);self.scroll.setWidget(self.content)
            self.scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground,True)
        else:self.content.setParent(self)
        self.grip=WhiteGrip(self);self.grip.setFixedSize(22,22);self.grip.setStyleSheet('background:transparent;')
        self.dock=DockManager(self,self.profile,self._dock_changed)
        c.windows.append(self);self.apply_profile()
    @property
    def locked(self):return self.controller.cfg['locked']
    def outline(self):
        return shape_path(QRectF(self.rect()).adjusted(1,1,-1,-1),self.profile['shape'],dock_edge=self.dock.dock_edge)
    def paintEvent(self,event):
        p=QPainter(self);p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source);p.fillRect(self.rect(),Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg=QColor('#1c2832' if self.controller.cfg['theme']=='dark' else '#f4f7f9');bg.setAlpha(round(255*(1-self.controller.cfg['transparency']/100)))
        p.setPen(QPen(QColor(128,155,166,135),1));p.setBrush(bg);p.drawPath(self.outline())
    def _dock_changed(self):self.update_shape();self.controller.save()
    def apply_profile(self,resize=True):
        shape=self.profile['shape']
        if self.role=='rail': minimum=(52,140) if shape not in ('圆形','正方形','三角形') else (170,170) if shape!='三角形' else (180,260)
        elif self.role=='details':minimum=(240,200) if shape not in ('圆形','正方形','三角形') else (340,340) if shape!='三角形' else (360,460)
        else:minimum=(330,350)
        self.setMinimumSize(*minimum)
        if resize:
            w=max(minimum[0],self.profile['width']);h=max(minimum[1],self.profile['height'])
            if shape in ('圆形','正方形'):w=h=max(w,h)
            self.resize(w,h)
        self.apply_theme();self.update_shape()
    def update_shape(self):
        path=self.outline();self.setMask(QRegion(path.toFillPolygon().toPolygon()))
        safe=content_rect(QRectF(self.rect()).adjusted(1,1,-1,-1),self.profile['shape'])
        if self.scroll:self.scroll.setGeometry(safe.adjusted(0,0,0,-24))
        else:self.content.setGeometry(safe)
        self.grip.move(grip_position(self.rect(),self.profile['shape']));self.grip.setVisible(not self.locked);self.grip.raise_();self.update()
        if self.role=='rail' and hasattr(self,'groups'):self.layout_rings(safe)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if not hasattr(self,'dock'):return
        if self.profile['shape'] in ('圆形','正方形') and self.width()!=self.height() and not self._square_adjusting:
            self._square_adjusting=True
            dw=abs(event.size().width()-event.oldSize().width());dh=abs(event.size().height()-event.oldSize().height())
            side=self.width() if dw>=dh else self.height();self.resize(side,side);self._square_adjusting=False
        font=QFont('Microsoft YaHei');font.setPixelSize(12 if self.role!='rail' else 10);self.setFont(font)
        self.update_shape()
        if hasattr(self,'save_timer'):self.save_timer.start(250)
    def apply_theme(self):
        dark=self.controller.cfg['theme']=='dark';fg='#edf8f6' if dark else '#263540';bg='#263540' if dark else '#f4f7f9'
        self.setStyleSheet('QWidget {color:'+fg+';background:transparent;} QToolButton {border:none;padding:3px;} QToolButton:hover {background:rgba(105,160,176,65);border-radius:5px;} QSlider::groove:horizontal{height:5px;background:rgba(128,155,166,120);} QSlider::handle:horizontal{width:15px;margin:-5px 0;background:#77a9d4;border-radius:7px;} QComboBox,QSpinBox{padding:4px;border:1px solid rgba(128,155,166,140);border-radius:4px;} QComboBox QAbstractItemView{background:'+bg+';color:'+fg+';} QScrollBar:vertical{width:7px;background:transparent;} QScrollBar::handle:vertical{background:rgba(128,155,166,140);min-height:15px;} QProgressBar{border:0;background:rgba(128,155,166,65);border-radius:2px;} QProgressBar::chunk{background:#77a9d4;border-radius:2px;}')
        if hasattr(self,'dock'):self.update_shape()
    def persist(self):
        self.profile.update(width=self.width(),height=self.height())
        if not self.dock.dock_edge:self.profile.update(x=self.x(),y=self.y())
        self.controller.save()

class Ring(QWidget):
    def __init__(self,key,controller):
        super().__init__();self.key=key;self.controller=controller;self.balance=None
        self.setMinimumSize(24,24);self.setMaximumSize(84,84);self.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Preferred)
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side=min(self.width(),self.height())-10;r=QRectF((self.width()-side)/2,(self.height()-side)/2,side,side)
        pen=QPen(QColor(128,155,166,90),4);p.setPen(pen);p.drawEllipse(r)
        if self.balance is not None:
            color='#4ade80' if self.balance>=16 else '#f0b429' if self.balance>=8 else '#ef6868'
            pen=QPen(QColor(color),4);pen.setCapStyle(Qt.PenCapStyle.RoundCap);p.setPen(pen)
            p.drawArc(r,90*16,-round(min(1,max(0,self.balance/20))*360*16))
        p.setPen(QColor('#edf8f6' if self.controller.cfg['theme']=='dark' else '#263540'))
        p.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,'DS' if self.key=='deepseek' else '米' if self.key=='mimo' else 'G')

class SourceRow(QWidget):
    def __init__(self,key,name,controller):
        super().__init__();self.key=key
        layout=QHBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(10)
        self.ring=Ring(key,controller);layout.addWidget(self.ring,0)
        texts=QVBoxLayout();texts.setSpacing(5);top=QHBoxLayout();self.name=QLabel(name);self.balance=QLabel('—')
        self.balance.setAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.name);top.addStretch();top.addWidget(self.balance);texts.addLayout(top)
        self.meta=QLabel('等待刷新');self.meta.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred);texts.addWidget(self.meta)
        layout.addLayout(texts,1)
    def update_state(self,data):
        value=data['balance'].get(self.key);self.ring.balance=value;self.ring.update()
        self.balance.setText('—' if value is None else '¥%.2f'%value)
        tokens=data['usage'].get(self.key,0);token=('%.1fM'%(tokens/1e6)) if tokens>=1e6 else ('%.0fK'%(tokens/1000)) if tokens>=1000 else str(tokens)
        status=data['source_status'].get(self.key);note={'expired':'Cookie 过期','error':'连接失败','missing_key':'无 API Key','missing_cookie':'无 Cookie','pending':'等待刷新'}.get(status,'')
        text='今日 %s tokens'%token+(' · '+note if note else '')
        self.meta.setText(text);self.meta.setToolTip(text)

class Details(GlassWindow):
    def __init__(self,c):
        super().__init__(c,'AI用量HUD · 详情',(240,200),'details')
        self.save_timer=QTimer(self);self.save_timer.setSingleShot(True);self.save_timer.timeout.connect(self.persist)
        root=QVBoxLayout(self.content);root.setContentsMargins(4,4,4,26);root.setSpacing(9)
        header=QHBoxLayout();header.addWidget(QLabel('AI 用量'));header.addStretch()
        self.theme_button=c.theme_button(self.content);header.addWidget(self.theme_button)
        settings=QToolButton();settings.setText('设置');settings.clicked.connect(c.open_settings);header.addWidget(settings)
        close=QToolButton();close.setText('×');close.clicked.connect(self.hide);header.addWidget(close);root.addLayout(header)
        self.rows={key:SourceRow(key,name,c) for key,name in [('deepseek','DeepSeek'),('mimo','MiMo')]}
        for row in self.rows.values():row.ring.hide();root.addWidget(row)
        self.quota_labels={};self.quota_progress={};self.quota_resets={}
        title=QLabel('GPT / Codex 额度');root.addWidget(title)
        for key,name in [('five_hour','五小时'),('weekly','每周')]:
            label=QLabel(name+' · 未获取');label.setWordWrap(True);self.quota_labels[key]=label;root.addWidget(label)
            progress=QProgressBar();progress.setTextVisible(False);progress.setRange(0,100);progress.setFixedHeight(5);self.quota_progress[key]=progress;root.addWidget(progress)
            reset=QLabel('重置时间：未获取');reset.setWordWrap(True);self.quota_resets[key]=reset;root.addWidget(reset)
        self.quota_updated=QLabel('额度等待刷新');self.quota_updated.setWordWrap(True);root.addWidget(self.quota_updated)
        self.clock=QLabel();self.cost=QLabel();self.updated=QLabel()
        for label in (self.clock,self.cost,self.updated):label.setWordWrap(True);root.addWidget(label)
        root.addStretch();self.content.setMinimumWidth(0)
        self.open_animation=QPropertyAnimation(self,b'windowOpacity',self);self.open_animation.setDuration(180);self.open_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.apply_profile();self.refresh_labels()
    def refresh_labels(self):
        data=self.controller.snapshot()
        for row in self.rows.values():row.update_state(data)
        peak=self.controller.core.is_peak(time.localtime());self.clock.setText(time.strftime('%H:%M')+('  峰时段·双倍' if peak else '  谷时段'))
        self.cost.setText('今日费用（估算）$%.3f'%sum(data['usage_cost'].values()))
        stamp=data.get('updated_at');self.updated.setText('余额更新 '+time.strftime('%H:%M:%S',time.localtime(stamp)) if stamp else '余额等待刷新')
        usage=self.controller.codex_usage;now=time.time()
        for key,name in [('five_hour','五小时'),('weekly','每周')]:
            window=usage.get(key);progress=self.quota_progress[key]
            if not window:
                self.quota_labels[key].setText(name+' · 未获取');self.quota_resets[key].setText('重置时间：未获取');progress.setValue(0);progress.setEnabled(False);continue
            used=window['used_percent'];remaining=window['remaining_percent'];progress.setEnabled(True);progress.setValue(round(used))
            self.quota_labels[key].setText('%s · 已用 %.0f%% / 剩余 %.0f%%'%(name,used,remaining))
            reset=window.get('reset_at')
            if reset:
                seconds=int(reset-now)
                countdown=('还剩%d天%d小时'%(seconds//86400,(seconds%86400)//3600)) if seconds>=86400 else ('还剩%d小时%d分'%(seconds//3600,(seconds%3600)//60)) if seconds>0 else '等待刷新重置状态'
                self.quota_resets[key].setText('重置 '+time.strftime('%m-%d %H:%M',time.localtime(reset))+' · '+countdown)
            else:self.quota_resets[key].setText('重置时间：未获取')
        stamp=usage.get('fetched_at');stale=usage.get('status')!='ok' or not stamp or now-stamp>600
        self.quota_updated.setText(('额度数据已过期 · ' if stale and any(usage.get(k) for k in ('five_hour','weekly')) else '额度未获取 · ' if not any(usage.get(k) for k in ('five_hour','weekly')) else '额度更新 ')+(time.strftime('%H:%M:%S',time.localtime(stamp)) if stamp else '等待刷新'))
        self.quota_updated.setToolTip(usage.get('error') or ('Codex 官方额度接口' if usage.get('source')=='codex_app_server' else '本机历史额度记录'))
    def position_next_to(self,rail):
        if self.dock.dock_edge:self.dock.ensure_reachable();return
        if self.profile.get('follow',True):
            area=rail.screen().availableGeometry();x=rail.x()+rail.width()+12
            if x+self.width()>area.right()+1:x=rail.x()-self.width()-12
            self.move(max(area.left(),min(x,area.right()-self.width()+1)),max(area.top(),min(rail.y()+(rail.height()-self.height())//2,area.bottom()-self.height()+1)))
    def open_next_to(self,rail):
        if self.isVisible():return
        self.position_next_to(rail);self.refresh_labels();self.open_animation.stop();self.setWindowOpacity(0.)
        self.show();self.raise_();self.dock.reveal();self.open_animation.setStartValue(0.);self.open_animation.setEndValue(1.);self.open_animation.start()
    def enterEvent(self,event):self.controller.hud.leave_timer.stop();self.controller.hud.dock.reveal();super().enterEvent(event)
    def leaveEvent(self,event):self.controller.hud.schedule_hide();super().leaveEvent(event)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self.controller,'hud') and self.isVisible():self.position_next_to(self.controller.hud)
    def closeEvent(self,event):self.hide();event.ignore()

class HUD(GlassWindow):
    def __init__(self,c):
        super().__init__(c,'AI用量HUD',(52,140),'rail')
        self.leave_timer=QTimer(self);self.leave_timer.setSingleShot(True);self.leave_timer.timeout.connect(self.maybe_hide_details)
        self.save_timer=QTimer(self);self.save_timer.setSingleShot(True);self.save_timer.timeout.connect(self.persist)
        self.grid=QGridLayout(self.content);self.grid.setContentsMargins(0,0,0,24);self.grid.setSpacing(4)
        self.rings={};self.groups=[];self.ring_labels=[]
        for key,name in [('deepseek','DeepSeek'),('mimo','MiMo'),('gpt','GPT')]:
            group=QWidget();column=QVBoxLayout(group);column.setContentsMargins(0,0,0,0);column.setSpacing(2)
            ring=Ring(key,c);self.rings[key]=ring;column.addWidget(ring,0,Qt.AlignmentFlag.AlignHCenter)
            label=QLabel(name);font=QFont('Microsoft YaHei');font.setPixelSize(10);label.setFont(font);label.setAlignment(Qt.AlignmentFlag.AlignCenter);column.addWidget(label);self.ring_labels.append(label)
            self.groups.append(group)
        self.apply_profile();self.refresh_labels()
        self.clock_timer=QTimer(self);self.clock_timer.timeout.connect(self.refresh_labels);self.clock_timer.start(30000)
    def layout_rings(self,safe):
        if not hasattr(self,'grid'):return
        columns=3 if safe.width()>=170 and safe.height()<180 else 2 if safe.width()>=80 and safe.height()<220 else 1
        rows=math.ceil(3/columns);side=max(26,min(52,int(min(safe.width()/columns-8,(safe.height()-24)/rows-20))))
        for i,group in enumerate(self.groups):
            self.grid.removeWidget(group);self.grid.addWidget(group,i//columns,i%columns,Qt.AlignmentFlag.AlignCenter)
            self.rings[['deepseek','mimo','gpt'][i]].setFixedSize(side,side)
            self.ring_labels[i].setText((['DS','米','GPT'] if safe.width()/columns<52 else ['DeepSeek','MiMo','GPT'])[i])
    def refresh_labels(self):
        data=self.controller.snapshot();usage=self.controller.codex_usage
        for key,ring in self.rings.items():
            if key=='gpt':
                window=usage.get('five_hour') or usage.get('weekly');ring.balance=None if not window else window['remaining_percent']/5
                ring.setToolTip('\n'.join(name+'：'+('剩余%.0f%%'%usage[key]['remaining_percent'] if usage.get(key) else '未获取') for key,name in [('five_hour','五小时'),('weekly','每周')]))
            else:ring.balance=data['balance'].get(key)
            ring.update()
        self.controller.details.refresh_labels()
    def can_auto_hide(self):
        if self.controller.drag or self.resizing:return False
        details=self.controller.details;settings=self.controller.settings
        return not (details.isVisible() and (details.underMouse() or details.resizing)) and not (settings and settings.isVisible())
    def enterEvent(self,event):
        self.leave_timer.stop();self.dock.reveal()
        if not self.controller.drag:self.controller.details.open_next_to(self)
        super().enterEvent(event)
    def leaveEvent(self,event):self.schedule_hide();super().leaveEvent(event)
    def schedule_hide(self):
        if self.controller.details.isVisible():self.leave_timer.start(180)
    def maybe_hide_details(self):
        details=self.controller.details
        if not details.isVisible() or self.controller.cfg['pinned']:return
        point=QCursor.pos()
        if self.frameGeometry().contains(point) or details.frameGeometry().contains(point):return
        if not self.can_auto_hide():self.leave_timer.start(180);return
        if details.dock.dock_edge:details.dock.schedule_hide();return
        details.hide();self.dock.schedule_hide()
    def moveEvent(self,event):
        super().moveEvent(event)
        if hasattr(self.controller,'details') and self.controller.details.isVisible() and not (self.controller.drag and self.controller.drag[0] is self.controller.details):self.controller.details.position_next_to(self)
    def contextMenuEvent(self,event):
        self.leave_timer.stop();menu=QMenu(self);menu.addAction('立即刷新',self.controller.refresh);menu.addAction('外观设置',self.controller.open_settings)
        lock=menu.addAction('锁定位置和大小');lock.setCheckable(True);lock.setChecked(self.locked);lock.triggered.connect(self.controller.set_locked)
        pin=menu.addAction('固定详情窗');pin.setCheckable(True);pin.setChecked(self.controller.cfg['pinned']);pin.triggered.connect(self.controller.set_pinned)
        menu.addAction('恢复默认布局',self.controller.reset_defaults);menu.addSeparator();menu.addAction('退出',QApplication.instance().quit);menu.exec(event.globalPos());self.schedule_hide()
    def closeEvent(self,event):self.persist();QApplication.instance().quit();event.accept()

class Settings(GlassWindow):
    def __init__(self,c):
        super().__init__(c,'HUD 外观设置',(330,350),'settings')
        root=QVBoxLayout(self.content);root.setContentsMargins(8,8,8,28);root.setSpacing(10)
        bar=QHBoxLayout();bar.addWidget(QLabel('外观设置'));bar.addStretch();bar.addWidget(c.theme_button(self.content));close=QToolButton();close.setText('×');close.clicked.connect(self.hide);bar.addWidget(close);root.addLayout(bar)
        self.opacity_label=QLabel();root.addWidget(self.opacity_label);self.opacity=QSlider(Qt.Orientation.Horizontal);self.opacity.setRange(0,85);self.opacity.setValue(c.cfg['transparency']);self.opacity.valueChanged.connect(self.opacity_changed);root.addWidget(self.opacity)
        self.target=QComboBox();self.target.addItems(['主挂件','详情窗']);root.addWidget(self.target)
        self.shape=QComboBox();self.shape.addItems(SHAPES);root.addWidget(self.shape)
        sizes=QHBoxLayout();sizes.addWidget(QLabel('宽'));self.width_input=QSpinBox();self.width_input.setRange(52,1600);sizes.addWidget(self.width_input)
        sizes.addWidget(QLabel('高'));self.height_input=QSpinBox();self.height_input.setRange(100,1400);sizes.addWidget(self.height_input);root.addLayout(sizes)
        self.lock_checkbox=QCheckBox('锁定位置和大小');self.lock_checkbox.setChecked(c.cfg['locked']);self.lock_checkbox.toggled.connect(c.set_locked);root.addWidget(self.lock_checkbox)
        self.reset_button=QToolButton();self.reset_button.setText('恢复默认布局');self.reset_button.clicked.connect(c.reset_defaults);root.addWidget(self.reset_button)
        hint=QLabel('拖白弧调整大小；小窗口可滚动查看详情。\n拖出屏幕自动贴边半隐藏，悬停滑出。\nGPT 只显示真实额度与重置时间，不提醒。');hint.setWordWrap(True);root.addWidget(hint);root.addStretch()
        self._syncing=False;self.target.currentIndexChanged.connect(self.sync_controls);self.shape.currentTextChanged.connect(self.shape_changed)
        self.width_input.valueChanged.connect(lambda value:self.size_changed('width',value));self.height_input.valueChanged.connect(lambda value:self.size_changed('height',value))
        self.sync_controls();self.opacity_label.setText('背景透明度：%d%%'%c.cfg['transparency']);self.apply_profile()
    def selected_role(self):return 'rail' if self.target.currentIndex()==0 else 'details'
    def sync_controls(self,*args):
        self._syncing=True;profile=self.controller.cfg['windows'][self.selected_role()]
        self.shape.setCurrentText(profile['shape']);self.width_input.setValue(profile['width']);self.height_input.setValue(profile['height']);self._syncing=False
    def opacity_changed(self,value):
        self.controller.cfg['transparency']=value;self.opacity_label.setText('背景透明度：%d%%'%value)
        for w in self.controller.windows:w.update()
        self.controller.save()
    def shape_changed(self,value):
        if self._syncing:return
        role=self.selected_role();profile=self.controller.cfg['windows'][role];profile['shape']=value
        defaults={'rail':{'圆形':(190,190),'正方形':(190,190),'三角形':(240,360),'胶囊':(80,300)},'details':{'圆形':(440,440),'正方形':(380,380),'三角形':(600,660),'胶囊':(360,500)}}
        if value in defaults[role]:profile.update(zip(('width','height'),defaults[role][value]))
        self.controller.apply_profiles();self.sync_controls()
    def size_changed(self,key,value):
        if self._syncing:return
        profile=self.controller.cfg['windows'][self.selected_role()];profile[key]=value
        if profile['shape'] in ('圆形','正方形'):profile['width']=profile['height']=value
        self.controller.apply_profiles();self.sync_controls()
    def closeEvent(self,event):self.hide();event.ignore()

class Controller(QObject):
    data_ready=Signal();quota_ready=Signal(dict)
    def __init__(self,app):
        super().__init__();self.app=app;self.cfg=load_config();self.windows=[];self.settings=None;self.drag=None
        self.refreshing=threading.Lock();self.quota_lock=threading.Lock();self.core=load_core()
        self.codex_usage={'status':'unavailable','fetched_at':None,'source':None,'five_hour':None,'weekly':None,'error':None}
        self.details=Details(self);self.hud=HUD(self)
        for w in (self.hud,self.details):w.move(w.profile['x'],w.profile['y']);w.dock.restore()
        self.hud.show();self.hud.dock.restore();self.data_ready.connect(self.hud.refresh_labels);self.quota_ready.connect(self.update_usage);app.installEventFilter(self)
        self.poll=QTimer(self);self.poll.timeout.connect(self.refresh_balances)
        self.quota_poll=QTimer(self);self.quota_poll.timeout.connect(self.refresh_quota)
        if not TEST:self.poll.start(60000);self.quota_poll.start(180000);self.refresh()
    def snapshot(self):
        with self.core._lock:return {key:dict(value) if isinstance(value,dict) else value for key,value in self.core.STATE.items()}
    def save(self):
        try:
            self.cfg.update(width=self.details.profile['width'],height=self.details.profile['height'],x=self.hud.profile['x'],y=self.hud.profile['y'])
            temp=CONFIG.with_suffix('.tmp');temp.write_text(json.dumps(self.cfg,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(CONFIG)
        except (OSError,AttributeError):pass
    def refresh(self):self.refresh_balances();self.refresh_quota()
    def refresh_balances(self):
        if TEST:return
        def worker():
            if not self.refreshing.acquire(False):return
            try:self.core.refresh();self.data_ready.emit()
            except Exception:traceback.print_exc()
            finally:self.refreshing.release()
        threading.Thread(target=worker,daemon=True).start()
    def refresh_quota(self):
        if TEST:return
        def worker():
            if not self.quota_lock.acquire(False):return
            try:
                try:
                    from hud_codex_usage import read_usage
                    result=read_usage(timeout=15)
                except ImportError:
                    from PySide6.QtCore import QProcess
                    candidates=[Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe',Path.home()/'AppData/Local/hermes/hermes-agent/.venv/Scripts/python.exe']
                    python=next((p for p in candidates if p.exists()),None)
                    if python is None:raise RuntimeError('本机额度读取运行时不可用')
                    process=QProcess();process.setProgram(str(python));process.setArguments([str(HERE/'hud_codex_usage.py')]);process.start()
                    if not process.waitForFinished(18000):process.kill();process.waitForFinished(1500);raise RuntimeError('额度读取超时')
                    result=json.loads(bytes(process.readAllStandardOutput()).decode('utf-8'))
                self.quota_ready.emit(result)
            except Exception:self.quota_ready.emit({'status':'unavailable','fetched_at':None,'source':None,'five_hour':None,'weekly':None,'error':'额度暂时无法读取'})
            finally:self.quota_lock.release()
        threading.Thread(target=worker,daemon=True).start()
    def update_usage(self,result):
        if not isinstance(result,dict):return
        if result.get('status')=='unavailable' and any(self.codex_usage.get(k) for k in ('five_hour','weekly')):
            self.codex_usage=dict(self.codex_usage,status='stale',error=result.get('error'))
        else:self.codex_usage=result
        self.hud.refresh_labels()
    def theme_button(self,parent):
        b=QToolButton(parent);b.setText('◐');b.setToolTip('切换深浅色');b.setFixedSize(28,28);b.clicked.connect(lambda checked=False,source=b:self.toggle_theme(source));return b
    def toggle_theme(self,source):
        origin=source.mapToGlobal(source.rect().center());frames=[]
        for w in self.windows:
            if not w.isVisible():continue
            for previous in w.findChildren(ThemeReveal):previous.timer.stop();previous.hide();previous.deleteLater()
            frames.append((w,w.grab()))
        self.cfg['theme']='light' if self.cfg['theme']=='dark' else 'dark'
        for w in self.windows:w.apply_theme()
        self.save();started=time.monotonic()
        for w,old in frames:ThemeReveal(w,old,origin,started)
    def apply_profiles(self):
        for w in (self.hud,self.details):w.apply_profile();w.dock.ensure_reachable();w.persist()
        if self.details.isVisible():self.details.position_next_to(self.hud)
        self.save()
    def set_locked(self,checked):
        self.cfg['locked']=bool(checked);self.drag=None
        for w in self.windows:w.update_shape()
        if self.settings and self.settings.lock_checkbox.isChecked()!=bool(checked):self.settings.lock_checkbox.setChecked(bool(checked))
        self.save()
    def reset_defaults(self):
        self.cfg.update(theme='dark',transparency=40,pinned=False,locked=False)
        defaults=default_profiles()
        for w in self.windows:
            w.dock.animation.stop();w.dock.timer.stop();w.dock.dock_edge=None;w.dock.hidden=False
            w.profile.clear();w.profile.update(defaults[w.role]);w.apply_profile();w.move(w.profile['x'],w.profile['y']);w.dock.ensure_reachable()
        self.details.hide()
        if self.settings:
            self.settings.opacity.setValue(40);self.settings.lock_checkbox.setChecked(False);self.settings.sync_controls()
        self.save()
    def open_settings(self):
        if self.settings is None:self.settings=Settings(self)
        if self.settings.dock.dock_edge:
            self.settings.show();self.settings.dock.restore();self.settings.dock.reveal();self.settings.raise_();return
        anchor=self.details if self.details.isVisible() else self.hud;area=self.hud.screen().availableGeometry()
        self.settings.move(max(area.left(),min(anchor.x()+anchor.width()+12,area.right()-self.settings.width()+1)),max(area.top(),min(self.hud.y(),area.bottom()-self.settings.height()+1)))
        self.settings.show();self.settings.raise_();self.settings.dock.reveal()
    def set_pinned(self,checked):
        self.cfg['pinned']=checked
        if checked:self.details.open_next_to(self.hud)
        else:self.hud.schedule_hide()
        self.save()
    def eventFilter(self,obj,event):
        if not isinstance(obj,QWidget):return False
        window=obj.window()
        if window not in self.windows:return False
        kind=event.type()
        if kind==QEvent.Type.MouseButtonPress and event.button()==Qt.MouseButton.LeftButton:
            if isinstance(obj,(QAbstractButton,QSlider,QComboBox,QSizeGrip,QSpinBox,QScrollBar)):return False
            if obj.parentWidget() and isinstance(obj.parentWidget(),(QComboBox,QSpinBox)):return False
            if self.cfg['locked']:return False
            self.hud.leave_timer.stop();window.dock.animation.stop();window.dock.timer.stop()
            self.drag=(window,event.globalPosition().toPoint()-window.pos());return True
        if kind==QEvent.Type.MouseMove and self.drag:
            w,offset=self.drag;w.move(event.globalPosition().toPoint()-offset);return True
        if kind==QEvent.Type.MouseButtonRelease and self.drag:
            w=self.drag[0];self.drag=None
            if w.role=='details':w.profile['follow']=False
            w.dock.finish_drag();w.persist();self.hud.schedule_hide();return True
        return False

if __name__=='__main__':
    try:
        app=QApplication(sys.argv);app.setFont(QFont('Microsoft YaHei',10));app.setQuitOnLastWindowClosed(False)
        from PySide6.QtNetwork import QLocalServer,QLocalSocket
        socket=QLocalSocket();socket.connectToServer('DGUT_AIUsageHUD_Qt')
        if not TEST and socket.waitForConnected(150):sys.exit(0)
        server=QLocalServer();server.listen('DGUT_AIUsageHUD_Qt' if not TEST else 'DGUT_AIUsageHUD_Qt_Test_'+str(os.getpid()))
        controller=Controller(app)
        if '--settings' in sys.argv:QTimer.singleShot(300,controller.open_settings)
        if TEST:
            testfile=os.environ.get('HUD_QT_TEST_SCRIPT')
            if testfile:exec(compile(Path(testfile).read_text(encoding='utf-8-sig'),testfile,'exec'),globals())
        app.aboutToQuit.connect(controller.hud.persist)
        app.exec()
    except Exception:traceback.print_exc();raise
