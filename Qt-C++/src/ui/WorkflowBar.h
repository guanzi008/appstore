#pragma once

#include <QFrame>

class QLabel;
class QPushButton;
class QResizeEvent;

class WorkflowBar final : public QFrame
{
    Q_OBJECT
    Q_DISABLE_COPY(WorkflowBar)

public:
    enum class Step {
        Parse,
        Capture,
        Wait,
        Submit,
    };

    enum class StepState {
        Idle,
        Running,
        Done,
        Failed,
    };

    explicit WorkflowBar(QWidget *parent = nullptr);

    void resetSteps();
    void setStepState(Step step, StepState state);
    void setStepStates(StepState parse, StepState capture, StepState wait, StepState submit);
    void setStatusText(const QString &text);
    void setBusy(bool busy);
    void setPublishControlsVisible(bool visible, bool draft);

signals:
    void submitRequested();
    void draftSaveRequested();

protected:
    void resizeEvent(QResizeEvent *event) override;

private:
    void updateStatusLabel();
    void updateStepLabel(QLabel *label, const QString &title, StepState state);
    static QString statePrefix(StepState state);
    static QString stateProperty(StepState state);

    QWidget *m_steps = nullptr;
    QLabel *m_parseStepLabel = nullptr;
    QLabel *m_captureStepLabel = nullptr;
    QLabel *m_waitStepLabel = nullptr;
    QLabel *m_submitStepLabel = nullptr;
    QLabel *m_statusLabel = nullptr;
    QPushButton *m_saveDraftButton = nullptr;
    QPushButton *m_submitButton = nullptr;
};
