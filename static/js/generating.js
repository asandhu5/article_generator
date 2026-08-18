(() => {
  const article = window.__ARTICLE__;
  if (article.status === "error") return;

  const progressFill = document.getElementById("progress-fill");
  const progressLabel = document.getElementById("progress-label");
  const currentStepText = document.getElementById("current-step-text");
  const stepLog = document.getElementById("step-log");

  let lastStep = null;

  function logStep(text) {
    if (text === lastStep) return;
    lastStep = text;
    const li = document.createElement("li");
    li.innerHTML = `<span class="dot"></span> ${text}`;
    stepLog.prepend(li);
    while (stepLog.children.length > 6) {
      stepLog.removeChild(stepLog.lastChild);
    }
  }

  async function poll() {
    try {
      const resp = await fetch(article.statusUrl);
      const data = await resp.json();

      if (data.status === "ready") {
        window.location.href = article.articleUrl;
        return;
      }
      if (data.status === "error") {
        window.location.reload();
        return;
      }

      const total = data.step_total || 0;
      const pct = total ? Math.min(100, Math.round((data.step_index / total) * 100)) : 4;
      progressFill.style.width = `${Math.max(pct, 4)}%`;
      progressLabel.textContent = total ? `Step ${data.step_index} of ${total}` : "Getting started…";
      currentStepText.textContent = data.current_step;
      logStep(data.current_step);
    } catch (err) {
      console.warn("Status check failed, retrying...", err);
    } finally {
      setTimeout(poll, 1500);
    }
  }

  poll();
})();
