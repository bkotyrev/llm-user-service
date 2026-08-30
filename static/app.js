const form = document.querySelector("#chatForm");
const input = document.querySelector("#messageInput");
const sendButton = document.querySelector("#sendButton");
const sendLabel = document.querySelector("#sendLabel");
const charCount = document.querySelector("#charCount");
const status = document.querySelector("#formStatus");
const resultPanel = document.querySelector("#resultPanel");
const resultText = document.querySelector("#resultText");
const resultMeta = document.querySelector("#resultMeta");
const requestMeta = document.querySelector("#requestMeta");

function setStatus(message = "", type = "") {
  status.textContent = message;
  status.className = "mt-3 min-h-5 text-sm";
  if (type === "error") status.classList.add("text-red-600");
  if (type === "info") status.classList.add("text-slate-500");
}

function updateCount() {
  charCount.textContent = `${input.value.length} / 1000`;
}

function setSubmitting(isSubmitting) {
  sendButton.disabled = isSubmitting;
  sendLabel.textContent = isSubmitting ? "Обработка…" : "Суммировать";
}

function showResult(payload) {
  resultPanel.classList.remove("hidden");
  resultMeta.classList.remove("hidden");
  resultText.textContent = payload.response || "";
  requestMeta.textContent = payload.request_id
    ? `ID запроса: ${payload.request_id}`
    : "";

  if (payload.fallback) {
    resultMeta.textContent = "временный ответ";
    resultMeta.className = "rounded-full bg-amber-100 px-2.5 py-1 text-xs font-medium text-amber-700";
  } else if (payload.cached) {
    resultMeta.textContent = "из кэша";
    resultMeta.className = "rounded-full bg-blue-100 px-2.5 py-1 text-xs font-medium text-blue-700";
  } else {
    resultMeta.textContent = "готово";
    resultMeta.className = "rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-medium text-emerald-700";
  }
}

input.addEventListener("input", updateCount);

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message) {
    setStatus("Введите текст для суммаризации.", "error");
    input.focus();
    return;
  }

  setSubmitting(true);
  setStatus();
  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    const payload = await response.json().catch(() => ({}));

    if (response.status === 503 && payload.fallback) {
      showResult(payload);
      setStatus("Модель временно недоступна.", "error");
      return;
    }
    if (!response.ok) {
      setStatus(payload.detail || "Не удалось обработать запрос.", "error");
      return;
    }
    showResult(payload);
  } catch {
    setStatus("Не удалось подключиться к сервису.", "error");
  } finally {
    setSubmitting(false);
  }
});

updateCount();
