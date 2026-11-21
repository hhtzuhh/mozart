"use strict";

function rehearsalChart(panel) {
  const plot = panel.querySelector("svg");
  let trials = [], budget, baseline;
  function add(tag, attributes, text) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key,value] of Object.entries(attributes)) node.setAttribute(key,value);
    if (text !== undefined) node.textContent = text;
    plot.append(node);
    return node;
  }
  function draw() {
    if (!budget || !Number.isFinite(baseline)) return;
    const width = Math.max(plot.clientWidth, 260);
    plot.setAttribute("viewBox", "0 0 " + width + " 152");
    plot.replaceChildren();
    const scores = [baseline, ...trials.map(r => r.trial_f1)].map(v => v*100);
    const lo = Math.floor((Math.min(...scores)-1)/5)*5, hi = Math.ceil((Math.max(...scores)+1)/5)*5;
    const x = n => 38 + (width-60)*n/budget;
    const y = value => 122 - (value-lo)/(hi-lo)*102;
    for (let tick=lo; tick<=hi; tick+=hi-lo>35?10:5) {
      add("line",{x1:x(0),x2:x(budget),y1:y(tick),y2:y(tick),stroke:"#dce4dd"});
      add("text",{x:28,y:y(tick)+4,"text-anchor":"end"},tick);
    }
    for (let n=0;n<=budget;n++) add("text",{x:x(n),y:146,"text-anchor":"middle"}, n?"R"+n:"Baseline");
    let best = -Infinity;
    const peaks = scores.map(v => best=Math.max(best,v));
    for (const [values,color,dash] of [[scores,"#285db2","none"],[peaks,"#16734c","6 5"]]) {
      add("polyline",{points:values.map((v,n)=>x(n)+","+y(v)).join(" "),fill:"none",stroke:color,"stroke-width":3,"stroke-dasharray":dash});
    }
    scores.forEach((v,n)=>{
      const point=add("circle",{cx:x(n),cy:y(v),r:4,fill:"#285db2"});
      const title=document.createElementNS(plot.namespaceURI,"title");
      title.textContent=(n?"Round "+n:"Baseline")+" · "+v.toFixed(2);point.append(title);
    });
  }
  new ResizeObserver(draw).observe(plot);
  return (rounds, limit, start) => {
    trials = rounds; budget = limit; baseline = start;
    panel.hidden = !Number.isFinite(start);
    draw();
  };
}
