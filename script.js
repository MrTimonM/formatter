function formatHeaders() {
  const input = document.getElementById("inputHeaders").value.trim();
  const lines = input.split("\n").filter(Boolean);
  let result = "";

  for (let i = 0; i < lines.length; i += 2) {
    const key = lines[i]?.trim();
    const value = lines[i + 1]?.trim();
    if (key && value) {
      result += `${key}: ${value}\n`;
    }
  }

  document.getElementById("outputHeaders").value = result.trim();
}

function copyToClipboard() {
  const output = document.getElementById("outputHeaders");
  output.select();
  output.setSelectionRange(0, 99999); // For mobile devices
  navigator.clipboard.writeText(output.value);
  alert("Formatted headers copied to clipboard!");
}
