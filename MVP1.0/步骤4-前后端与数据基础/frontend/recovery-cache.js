'use strict';
// Private local drafts/outbox only. Authentication and saved history remain server-owned.
class DurableRecoveryCache {
 constructor(store){this.store=store;this.prefix='weiwan-recovery-v1:';this.available=!!store;}
 key(user,cid){return this.prefix+user+':'+cid;}
 read(user,cid){
  if(!this.store)return {};
  try{const record=JSON.parse(this.store.getItem(this.key(user,cid))||'{}');
   return record.user_id===user&&record.conversation_id===cid?record:{};
  }catch{return {};}
 }
 write(user,cid,record){
  if(!this.store){this.available=false;return false;}
  const safe={user_id:user,conversation_id:cid,updated_at:Date.now()};
  for(const field of ['draft','pending','view','candidate_intent','stop_intent','candidateView']){
   if(record[field]!==undefined)safe[field]=record[field];
  }
  try{this.store.setItem(this.key(user,cid),JSON.stringify(safe));return true;}
  catch{this.available=false;return false;}
 }
 restrictTo(user){
  if(!this.store)return;
  try{const remove=[];for(let i=0;i<this.store.length;i++){
    const key=this.store.key(i);if(key?.startsWith(this.prefix)&&(!user||!key.startsWith(this.prefix+user+':')))remove.push(key);
   }for(const key of remove)this.store.removeItem(key);
  }catch{this.available=false;}
 }
 clear(){this.restrictTo(null);}
}
const recoveryCache=(()=>{try{return new DurableRecoveryCache(localStorage);}catch{return new DurableRecoveryCache(null);}})();
