const html = "<div class=\"tabs\">\n  <div class=\"tab active\" data-v=\"live\">Transcript</div>\n  <div class=\"tab\" data-v=\"speakers\">Speakers</div>\n  <div class=\"tab\" data-v=\"settings\">Settings</div>\n  <div class=\"tab\" data-v=\"log\">Console</div>\n  <div class=\"search-wrap\" id=\"search-wrap\">\n    <div id=\"searchbox\" class=\"searchbox\">\n      <span id=\"search-ic\" class=\"search-ic\"></span>\n      <input id=\"search-input\" type=\"text\" autocomplete=\"off\" spellcheck=\"false\" placeholder=\"Search\" />\n      <span id=\"search-clear\" class=\"search-clear\" title=\"Clear search\"></span>\n    </div>\n    <div id=\"search-suggest\" class=\"search-suggest\"></div>\n  </div>\n</div>";

export function SearchTabs() {
  return <div style={{ display: "contents" }} dangerouslySetInnerHTML={{ __html: html }} />;
}
