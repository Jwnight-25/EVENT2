import { useEffect, useRef, useState, type ReactNode } from 'react';
import * as echarts from 'echarts';
import { LoaderCircle, X, FileSearch } from 'lucide-react';
import { api, post, statusText, type Job } from './api';

export function Chart({ option, height = 350 }: { option: echarts.EChartsOption; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const chart = echarts.init(ref.current!);
    chart.setOption(option);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current!);
    return () => { observer.disconnect(); chart.dispose(); };
  }, [option]);
  return <div ref={ref} style={{ height, width: '100%' }} role="img" aria-label="分析图表" />;
}
export const axis = { axisLine: { lineStyle: { color: '#dde3dd' } }, axisLabel: { color: '#78837c', fontSize: 11 }, splitLine: { lineStyle: { color: '#eef1ec' } } };
export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="empty"><FileSearch size={34} strokeWidth={1.3}/><h3>{title}</h3><p>{children}</p></div>;
}
export function Modal({ title, close, children }: { title: string; close: () => void; children: ReactNode }) {
  return <div className="overlay" onClick={close}><section className="modal" role="dialog" aria-modal="true" aria-label={title} onClick={e => e.stopPropagation()}><header><h2>{title}</h2><button className="icon-btn" onClick={close} aria-label="关闭"><X size={20}/></button></header>{children}</section></div>;
}
export function useLoad<T>(loader: () => Promise<T>, dependencies: unknown[]) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    let active = true;
    setData(undefined); setError(''); setLoading(true);
    loader().then(value => active && setData(value)).catch(e => active && setError(e.message)).finally(() => active && setLoading(false));
    return () => { active = false; };
  }, dependencies);
  return { data, error, loading };
}
export function JobPanel({ id, finished }: { id: string; finished: () => void }) {
  const [job, setJob] = useState<Job>();
  const [error, setError] = useState('');
  const finishRef = useRef(finished); finishRef.current = finished;
  useEffect(() => {
    let active = true; let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const value = await api<Job>('/jobs/' + id);
        if (!active) return;
        setJob(value); setError('');
        if (['succeeded', 'failed', 'cancelled'].includes(value.status)) finishRef.current();
        else timer = setTimeout(poll, 1500);
      } catch (e) { if (active) { setError((e as Error).message); timer = setTimeout(poll, 3000); } }
    }
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [id]);
  const busy = !job || ['queued', 'running'].includes(job.status);
  return <div className="job-panel"><div className="row"><strong>{busy && <LoaderCircle className="spin" size={16}/>} {job ? statusText[job.status] : '读取任务'}</strong><span className="muted">{job?.stage}</span>{busy && <button className="text-btn" onClick={() => post('/jobs/' + id + '/cancel', {}).catch(e => setError(e.message))}>取消任务</button>}</div>{job?.progress != null && <progress max="100" value={job.progress}/>}<small>任务 {id.slice(0, 8)} · 训练需要独立工作进程运行</small>{(error || job?.error) && <p className="error">{error || job?.error}</p>}</div>;
}
