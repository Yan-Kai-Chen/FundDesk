type StorageLike = Pick<Storage,'getItem'|'setItem'>;
const PAGES = new Set(['workspace','records','research','data','settings']);

export function restoredPage(storage:StorageLike,mode:string,hasFundLink=false) {
  if(hasFundLink)return 'workspace';
  const value=storage.getItem('funding-page-'+mode)||'workspace';
  return PAGES.has(value)?value:'workspace';
}

export function rememberPage(storage:StorageLike,mode:string,page:string) {
  if(PAGES.has(page))storage.setItem('funding-page-'+mode,page);
}
