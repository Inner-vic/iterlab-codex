import {readFile,writeFile,copyFile,mkdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {dirname,join,resolve,delimiter} from 'node:path';
const require = createRequire(import.meta.url);
const {build} = require('esbuild');
const out = resolve(process.env.ITERLAB_GRAPH_OUT || '../../static/vendor');
await mkdir(out,{recursive:true});
await build({entryPoints:['index.mjs'],nodePaths:(process.env.NODE_PATH||'').split(delimiter).filter(Boolean),bundle:true,format:'iife',target:['es2022'],outfile:join(out,'exploration-map.js'),minify:true,legalComments:'linked',banner:{js:'/* IterLab research exploration map. Independent Codex host with pointer panning. Third-party licenses: exploration-map.NOTICE.txt */'}});
await copyFile('exploration-map.css',join(out,'exploration-map.css'));
const packages = ['@antv/x6','@dagrejs/dagre','@dagrejs/graphlib','dom-align','lodash-es','mousetrap','utility-types','tslib'];
let notice = 'IterLab research exploration map — bundled dependency notices\n\nSources: https://github.com/antvis/X6 and https://github.com/dagrejs/dagre\nBuilt locally; no runtime CDN. Exact versions are in frontend/exploration-map/package-lock.json.\n';
for (const name of packages) {
  const directory = dirname(require.resolve(`${name}/package.json`));
  const metadata = JSON.parse(await readFile(join(directory,'package.json'),'utf8'));
  let license = '';
  for (const filename of ['LICENSE','LICENSE.md','LICENSE.txt','License','license','LICENCE','LICENSE.txt','CopyrightNotice.txt']) {try{license=await readFile(join(directory,filename),'utf8');break;}catch{}}
  if (!license) throw Error(`Missing license for ${name}`);
  notice += `\n\n===== ${name} ${metadata.version} (${metadata.license}) =====\n${license}`;
}
await writeFile(join(out,'exploration-map.NOTICE.txt'),notice);
console.log('Built exploration-map.js, exploration-map.css and license notices.');
