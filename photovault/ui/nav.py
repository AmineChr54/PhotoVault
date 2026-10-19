"""Sidebar item painter: line icon + label, soft-glow accent bar on the active page."""
from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from . import theme
from .icons import icon_pixmap, qcolor

ICON_ROLE = Qt.UserRole + 1


class NavDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(option.rect.width(), theme.px(42))

    def paint(self, p: QPainter, option, index):
        t = theme.tokens()
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        r = QRectF(option.rect).adjusted(theme.px(10), theme.px(2), -theme.px(10), -theme.px(2))
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        radius = theme.px(10)
        if selected:
            grad = QLinearGradient(r.topLeft(), r.topRight())
            grad.setColorAt(0, qcolor(t["accent_soft"]))
            end = qcolor(t["accent_soft"])
            end.setAlpha(6)
            grad.setColorAt(1, end)
            p.setPen(Qt.NoPen)
            p.setBrush(grad)
            p.drawRoundedRect(r, radius, radius)
            # 3px accent bar flush with the sidebar edge, with a soft glow around it
            bar_h = r.height() * 0.62
            bar = QRectF(option.rect.left(), r.center().y() - bar_h / 2, theme.px(3), bar_h)
            acc = qcolor(t["accent"])
            for grow, alpha in ((theme.px(7), 14), (theme.px(4), 30), (theme.px(2), 60)):
                glow = QColor(acc)
                glow.setAlpha(alpha)
                p.setBrush(glow)
                p.drawRoundedRect(bar.adjusted(0, -grow, grow, grow), grow, grow)
            p.setBrush(acc)
            p.drawRoundedRect(bar, 1.5, 1.5)
        elif hover:
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(t["s3"]))
            p.drawRoundedRect(r, radius, radius)

        color = t["accent_text"] if selected else t["text2"]
        name = index.data(ICON_ROLE)
        isz = theme.px(20)
        x = r.left() + theme.px(14)
        if name:
            dpr = p.device().devicePixelRatio() if p.device() else 1.0
            pm = icon_pixmap(name, color, isz, dpr)
            p.drawPixmap(QPointF(x, r.center().y() - isz / 2), pm)
        font = option.font
        font.setWeight(font.Weight.DemiBold if selected else font.Weight.Normal)
        p.setFont(font)
        p.setPen(qcolor(t["text"] if selected else t["text2"] if not hover else t["text"]))
        tx = x + isz + theme.px(12)
        text_rect = QRectF(tx, r.top(), r.right() - tx - theme.px(8), r.height())
        text = p.fontMetrics().elidedText(index.data(Qt.DisplayRole) or "", Qt.ElideRight, int(text_rect.width()))
        p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, text)
        p.restore()
