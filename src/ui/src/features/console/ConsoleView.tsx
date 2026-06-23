const html = "  <section id=\"v-log\" class=\"view\">\n    <div class=\"log-bar\">\n      <button id=\"logcopy\" class=\"sec\">Copy log</button>\n      <button id=\"logclear\" class=\"sec\">Clear</button>\n      <span class=\"hint\">Select any text to copy just part of it.</span>\n    </div>\n    <div id=\"log\"></div>";

export function ConsoleView() {
  return <div style={{ display: "contents" }} dangerouslySetInnerHTML={{ __html: html }} />;
}
