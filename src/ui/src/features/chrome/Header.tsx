const html = "<header>\n  <h1>Discord Live Transcriber</h1>\n  <span class=\"pill\"><span id=\"bdot\" class=\"dot\"></span><span id=\"bstat\">stopped</span></span>\n  <span class=\"pill\"><span id=\"rdot\" class=\"dot\"></span><span id=\"rstat\">relay</span></span>\n  <span class=\"pill\" id=\"activepill\" title=\"active audio streams\">0 streams</span>\n  <span class=\"grow\"></span>\n  <button id=\"startbtn\">Start</button>\n  <button id=\"stopbtn\" class=\"danger\" disabled>Stop</button>\n</header>";

export function Header() {
  return <div style={{ display: "contents" }} dangerouslySetInnerHTML={{ __html: html }} />;
}
