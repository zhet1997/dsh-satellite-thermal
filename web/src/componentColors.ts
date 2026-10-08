const palette=['#3b82f6','#e69138','#36a681','#a16bd2','#df6b83','#20a5b5','#91a83a','#bc7657','#7184d7','#c74daf','#d4ac28','#50957c'];
export function componentColor(id:string){let hash=0;for(const ch of id)hash=(Math.imul(hash,31)+ch.charCodeAt(0))>>>0;return palette[hash%palette.length];}
