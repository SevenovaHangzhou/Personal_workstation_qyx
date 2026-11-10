"""A fixed native diagram renderer for data-only teaching flows."""
from PyQt5.QtCore import Qt, QPointF
from PyQt5.QtGui import QColor, QBrush, QPen, QFont, QPainter, QPainterPath, QPolygonF
from PyQt5.QtWidgets import QFrame, QGraphicsScene, QGraphicsView, QGraphicsTextItem, QComboBox
from dag_layout import layers_for
from ui_helpers import box, label, button


class DiagramView(QGraphicsView):
    def resizeEvent(self,event):
        super().resizeEvent(event)
        width=self.sceneRect().width()
        if width:
            scale=max(.85,min(1.,(self.viewport().width()-12)/width))
            self.resetTransform(); self.scale(scale,scale)


class LessonDiagram(QFrame):
    def __init__(self,block,parent=None):
        super().__init__(parent); self.setObjectName('quiet')
        self.block=block; self.nodes={n['id']:n for n in block['nodes']}; self.rects={}
        layout=box(self,margins=(12,12,12,12),spacing=9)
        layout.addWidget(label(block['title'],'h2',True))
        layout.addWidget(label(block['caption'],'muted',True))
        layers=layers_for(self.nodes,[(e['source'],e['target']) for e in block['edges']])
        self.order=[key for layer in layers for key in layer]
        self.scene=QGraphicsScene(self); self.view=DiagramView(self.scene)
        self.view.setRenderHint(QPainter.Antialiasing)
        self.view.setAccessibleName('教学流程图：'+block['title'])
        self.view.setStyleSheet('background:#f8fafc; border:0;')
        self.view.setMinimumWidth(120); self.view.setFixedHeight(min(360,45+len(layers)*100))
        positions={}; width=max(map(len,layers))*204
        self.titles={}; heights={}; ranks={}
        for index,key in enumerate(self.order,1):
            text=QGraphicsTextItem(); text.setPlainText(str(index)+' · '+self.nodes[key]['title'])
            text.setTextWidth(164); text.setFont(QFont('Noto Sans CJK SC',10))
            text.setToolTip(self.nodes[key]['title']); self.titles[key]=text
            heights[key]=max(58,text.boundingRect().height()+10)
        y=12
        for row,layer in enumerate(layers):
            for col,key in enumerate(layer):
                positions[key]=QPointF((width-len(layer)*204)/2+col*204+12,y); ranks[key]=row
            y+=max(heights[key] for key in layer)+50
        for edge_index,edge in enumerate(block['edges']):
            a=positions[edge['source']]+QPointF(88,heights[edge['source']]); b=positions[edge['target']]+QPointF(88,0)
            path=QPainterPath(a); middle=(a.y()+b.y())/2
            if ranks[edge['target']]-ranks[edge['source']]>1:
                # Long edges use a side corridor instead of crossing another node.
                corridor=width+12+edge_index*10
                path.lineTo(a.x(),a.y()+16); path.lineTo(corridor,a.y()+16)
                path.lineTo(corridor,b.y()-16); path.lineTo(b.x(),b.y()-16); path.lineTo(b)
            else:
                path.lineTo(a.x(),middle); path.lineTo(b.x(),middle); path.lineTo(b)
            self.scene.addPath(path,QPen(QColor('#92adc1'),1.5))
            self.scene.addPolygon(QPolygonF([b,b+QPointF(-4,-7),b+QPointF(4,-7)]),QPen(Qt.NoPen),QBrush(QColor('#426c83')))
            if edge['label']:
                text=QGraphicsTextItem(); text.setPlainText(edge['label'][:12]+('…' if len(edge['label'])>12 else '')); text.setTextWidth(165)
                text.setToolTip(edge['label'])
                text.setFont(QFont('Noto Sans CJK SC',9)); text.setDefaultTextColor(QColor('#5f7488'))
                text.setPos(min(a.x(),b.x())+5,middle-19); self.scene.addItem(text)
        for index,key in enumerate(self.order,1):
            p=positions[key]
            rect=self.scene.addRect(p.x(),p.y(),176,heights[key],QPen(QColor('#cdd9e3')),QBrush(QColor('#ffffff')))
            text=self.titles[key]; text.setPos(p+QPointF(6,5))
            self.scene.addItem(text); self.rects[key]=rect
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-10,-8,10,8)); layout.addWidget(self.view)
        controls=box(horizontal=True,spacing=6)
        self.previous=button('上一步','soft',callback=lambda:self.move(-1)); controls.addWidget(self.previous)
        self.selector=QComboBox(); self.selector.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.selector.setMinimumContentsLength(6)
        for i,key in enumerate(self.order,1): self.selector.addItem(str(i)+' · '+self.nodes[key]['title'],key)
        controls.addWidget(self.selector,1)
        self.next=button('下一步','soft',callback=lambda:self.move(1)); controls.addWidget(self.next); layout.addLayout(controls)
        self.explanation=label('',wrap=True); self.explanation.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.explanation.setAccessibleName('图解当前步骤说明'); layout.addWidget(self.explanation)
        self.selector.currentIndexChanged.connect(self.select); self.select(0)

    def move(self,offset):
        self.selector.setCurrentIndex(max(0,min(len(self.order)-1,self.selector.currentIndex()+offset)))

    def select(self,index):
        if not 0<=index<len(self.order): return
        key=self.order[index]
        for identifier,rect in self.rects.items():
            rect.setBrush(QBrush(QColor('#dce8f0' if identifier==key else '#ffffff')))
            rect.setPen(QPen(QColor('#426c83' if identifier==key else '#cdd9e3'),2 if identifier==key else 1))
        links=[edge['label']+' → '+self.nodes[edge['target']]['title'] for edge in self.block['edges']
               if edge['source']==key and edge['label']]
        self.explanation.setText(str(index+1)+' / '+str(len(self.order))+' · '+self.nodes[key]['explanation']+
                                 ('\n连接：'+'；'.join(links) if links else ''))
        self.previous.setEnabled(index>0); self.next.setEnabled(index<len(self.order)-1)
        self.view.ensureVisible(self.rects[key])
