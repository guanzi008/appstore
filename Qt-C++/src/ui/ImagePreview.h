#pragma once

#include <QImage>
#include <QImageReader>
#include <QLabel>
#include <QPainter>

class ImagePreview final : public QLabel
{
public:
    using QLabel::QLabel;

    QSize setImagePath(const QString &path, const QString &fallback)
    {
        QImageReader reader(path);
        reader.setAutoTransform(true);
        m_image = path.isEmpty() ? QImage() : reader.read();
        setText(m_image.isNull() ? fallback : QString());
        update();
        return m_image.size();
    }

protected:
    void paintEvent(QPaintEvent *event) override
    {
        QLabel::paintEvent(event);
        if (m_image.isNull()) {
            return;
        }

        const QRect bounds = contentsRect();
        if (bounds.isEmpty()) {
            return;
        }
        const QSize fitted = m_image.size().scaled(bounds.size(), Qt::KeepAspectRatio).expandedTo(QSize(1, 1));
        QRect target(QPoint(), fitted);
        target.moveCenter(bounds.center());
        QPainter painter(this);
        painter.setRenderHint(QPainter::SmoothPixmapTransform);
        painter.drawImage(target, m_image);
    }

private:
    QImage m_image;
};
