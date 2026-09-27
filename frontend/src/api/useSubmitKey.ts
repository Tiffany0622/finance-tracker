import { useRef } from 'react';

export function useSubmitKey() {
  const last = useRef({body:'',key:''});
  return (body:unknown) => {const encoded=JSON.stringify(body);if(last.current.body!==encoded)last.current={body:encoded,key:crypto.randomUUID()};return last.current.key;};
}
