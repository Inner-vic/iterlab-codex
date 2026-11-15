// Development-only resolution of an explicitly supplied existing dependency
// cache. Neither the application nor its browser bundle imports this loader.
import {registerHooks,createRequire} from 'node:module';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
const directory=process.env.ITERLAB_TEST_NODE_MODULES;
if(!directory)throw Error('Set ITERLAB_TEST_NODE_MODULES to an existing locked development dependency cache');
const dependency=createRequire(pathToFileURL(resolve(directory,'../package.json')));
registerHooks({resolve(specifier,context,next){
  try{return next(specifier,context);}catch(error){
    if(error.code!=='ERR_MODULE_NOT_FOUND'||specifier!=='@dagrejs/dagre')throw error;
    return {url:pathToFileURL(dependency.resolve(specifier)).href,shortCircuit:true};
  }
}});
