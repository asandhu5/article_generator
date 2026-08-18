(() => {
  const form = document.getElementById("setup-form");
  const toneGrid = document.getElementById("tone-grid");
  const errorBox = document.getElementById("form-error");
  const startBtn = document.getElementById("start-btn");
  const wordCountInput = document.getElementById("word_count");
  const wordCountValue = document.getElementById("word_count_value");
  const temperatureInput = document.getElementById("temperature");
  const temperatureValue = document.getElementById("temperature_value");

  wordCountInput.addEventListener("input", () => {
    wordCountValue.textContent = wordCountInput.value;
  });
  temperatureInput.addEventListener("input", () => {
    temperatureValue.textContent = parseFloat(temperatureInput.value).toFixed(1);
  });

  toneGrid.addEventListener("change", () => {
    const selected = toneGrid.querySelector("input[name=tone]:checked").value;
    toneGrid.querySelectorAll(".option-card").forEach((card) => {
      card.classList.toggle("selected", card.dataset.tone === selected);
    });
  });

  function showError(message) {
    errorBox.textContent = message;
    errorBox.style.display = "block";
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorBox.style.display = "none";

    const payload = {
      topic: document.getElementById("topic").value,
      audience: document.getElementById("audience").value,
      tone: toneGrid.querySelector("input[name=tone]:checked").value,
      word_count: wordCountInput.value,
      model: document.getElementById("model").value,
      temperature: temperatureInput.value,
    };

    if (!payload.topic.trim()) {
      showError("Please enter a topic.");
      return;
    }
    if (!payload.audience.trim()) {
      showError("Please enter a target audience.");
      return;
    }

    startBtn.disabled = true;
    startBtn.textContent = "Starting…";

    try {
      const resp = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      if (!resp.ok) {
        throw new Error(data.error || "Something went wrong starting generation.");
      }
      window.location.href = `/generate/${data.article_id}`;
    } catch (err) {
      showError(err.message);
      startBtn.disabled = false;
      startBtn.textContent = "Generate Article";
    }
  });
})();
