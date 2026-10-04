document.addEventListener("DOMContentLoaded", function () {
    const button = document.getElementById("parse-preview-button");
    if (!button) return;
    const status = document.getElementById("parse-preview-status");
    const result = document.getElementById("parse-preview-result");
    const lines = document.getElementById("parse-preview-lines");
    button.addEventListener("click", async function () {
        button.disabled = true;
        status.textContent = "Проверяем…";
        result.textContent = "";
        lines.textContent = "";
        try {
            const response = await fetch(button.dataset.url, {
                method: "POST",
                body: new FormData(button.closest("form")),
                credentials: "same-origin",
            });
            const data = await response.json();
            if (!response.ok) {
                const errors = Object.values(data.errors || {}).flat();
                status.textContent = errors.map(error => error.message).join(" ") || "Не удалось проверить разбор.";
                return;
            }
            status.textContent = data.text ? "Описание для поиска и пересылки:" : "Описание пустое — пост будет пропущен.";
            result.textContent = data.text;
            lines.textContent = data.numbered_original;
        } catch (error) {
            status.textContent = "Не удалось проверить разбор. Обновите страницу и повторите попытку.";
        } finally {
            button.disabled = false;
        }
    });
});
