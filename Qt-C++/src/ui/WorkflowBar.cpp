#include "ui/WorkflowBar.h"

#include <QHBoxLayout>
#include <QLabel>
#include <QPushButton>
#include <QSizePolicy>
#include <QStyle>
#include <QVariant>

namespace {

QLabel *makeStepLabel(QWidget *parent)
{
    auto *label = new QLabel(parent);
    label->setObjectName(QStringLiteral("WorkflowStep"));
    label->setAlignment(Qt::AlignCenter);
    label->setMinimumWidth(66);
    label->setFixedHeight(30);
    return label;
}

QLabel *makeStepSeparator(QWidget *parent)
{
    auto *label = new QLabel(QStringLiteral("->"), parent);
    label->setObjectName(QStringLiteral("MutedText"));
    label->setAlignment(Qt::AlignCenter);
    return label;
}

} // namespace

WorkflowBar::WorkflowBar(QWidget *parent)
    : QFrame(parent)
{
    setObjectName(QStringLiteral("WorkflowBar"));
    setFixedHeight(56);

    auto *layout = new QHBoxLayout(this);
    layout->setContentsMargins(12, 6, 12, 6);
    layout->setSpacing(10);
    layout->setAlignment(Qt::AlignVCenter);

    auto *stepsLayout = new QHBoxLayout();
    stepsLayout->setContentsMargins(0, 0, 0, 0);
    stepsLayout->setSpacing(5);
    m_parseStepLabel = makeStepLabel(this);
    m_captureStepLabel = makeStepLabel(this);
    m_waitStepLabel = makeStepLabel(this);
    m_submitStepLabel = makeStepLabel(this);
    stepsLayout->addWidget(m_parseStepLabel);
    stepsLayout->addWidget(makeStepSeparator(this));
    stepsLayout->addWidget(m_captureStepLabel);
    stepsLayout->addWidget(makeStepSeparator(this));
    stepsLayout->addWidget(m_waitStepLabel);
    stepsLayout->addWidget(makeStepSeparator(this));
    stepsLayout->addWidget(m_submitStepLabel);
    stepsLayout->addStretch(1);
    layout->addLayout(stepsLayout, 1);

    m_statusLabel = new QLabel(QStringLiteral("拖入包文件开始。"), this);
    m_statusLabel->setObjectName(QStringLiteral("MutedText"));
    m_statusLabel->setAlignment(Qt::AlignRight | Qt::AlignVCenter);
    m_statusLabel->setMinimumWidth(180);
    m_statusLabel->setMaximumWidth(360);
    m_statusLabel->setFixedHeight(30);
    m_statusLabel->setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Fixed);
    layout->addWidget(m_statusLabel, 0, Qt::AlignVCenter);

    m_submitButton = new QPushButton(style()->standardIcon(QStyle::SP_ArrowForward), QStringLiteral("执行全自动提交"), this);
    m_submitButton->setObjectName(QStringLiteral("PrimaryAction"));
    m_submitButton->setCursor(Qt::PointingHandCursor);
    m_submitButton->setFixedHeight(38);
    connect(m_submitButton, &QPushButton::clicked, this, &WorkflowBar::submitRequested);
    layout->addWidget(m_submitButton, 0, Qt::AlignVCenter);

    resetSteps();
}

void WorkflowBar::resetSteps()
{
    setStepStates(StepState::Idle, StepState::Idle, StepState::Idle, StepState::Idle);
}

void WorkflowBar::setStepState(Step step, StepState state)
{
    switch (step) {
    case Step::Parse:
        updateStepLabel(m_parseStepLabel, QStringLiteral("解析"), state);
        break;
    case Step::Capture:
        updateStepLabel(m_captureStepLabel, QStringLiteral("制图"), state);
        break;
    case Step::Wait:
        updateStepLabel(m_waitStepLabel, QStringLiteral("等待"), state);
        break;
    case Step::Submit:
        updateStepLabel(m_submitStepLabel, QStringLiteral("提交"), state);
        break;
    }
}

void WorkflowBar::setStepStates(StepState parse, StepState capture, StepState wait, StepState submit)
{
    setStepState(Step::Parse, parse);
    setStepState(Step::Capture, capture);
    setStepState(Step::Wait, wait);
    setStepState(Step::Submit, submit);
}

void WorkflowBar::setStatusText(const QString &text)
{
    m_statusLabel->setToolTip(text);
    m_statusLabel->setText(m_statusLabel->fontMetrics().elidedText(text, Qt::ElideMiddle, m_statusLabel->maximumWidth()));
}

void WorkflowBar::setBusy(bool busy)
{
    m_submitButton->setEnabled(!busy);
}

void WorkflowBar::updateStepLabel(QLabel *label, const QString &title, StepState state)
{
    if (label == nullptr) {
        return;
    }
    label->setText(QStringLiteral("%1 %2").arg(statePrefix(state), title));
    label->setProperty("state", QVariant(stateProperty(state)));
    label->style()->unpolish(label);
    label->style()->polish(label);
    label->update();
}

QString WorkflowBar::statePrefix(StepState state)
{
    switch (state) {
    case StepState::Running:
        return QStringLiteral("进行中");
    case StepState::Done:
        return QStringLiteral("完成");
    case StepState::Failed:
        return QStringLiteral("失败");
    case StepState::Idle:
        return QStringLiteral("待");
    }
    return QStringLiteral("待");
}

QString WorkflowBar::stateProperty(StepState state)
{
    switch (state) {
    case StepState::Running:
        return QStringLiteral("running");
    case StepState::Done:
        return QStringLiteral("done");
    case StepState::Failed:
        return QStringLiteral("failed");
    case StepState::Idle:
        return QStringLiteral("idle");
    }
    return QStringLiteral("idle");
}
