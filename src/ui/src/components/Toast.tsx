const html = "  <div id=\"toast\" class=\"toast\"><span class=\"tdot\"></span><span id=\"toasttext\">Saved</span></div>";

export function Toast() {
  return <div style={{ display: "contents" }} dangerouslySetInnerHTML={{ __html: html }} />;
}
