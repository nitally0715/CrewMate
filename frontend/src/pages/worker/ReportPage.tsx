import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { api } from '../../api/client';
import type { SpecReportRequest, SpecReportResponse, Trade, Worker } from '../../api/types';

const TARGET_OPTIONS = [
  '건축목공시공', '철근콘크리트시공', '조적미장시공', '타일석공시공', '방수시공',
  '도장시공', '비계시공', '배관시공', '용접', '건설기계운전',
];

const DEFAULT_TARGET: Partial<Record<Trade, string>> = {
  FORMWORK: '철근콘크리트시공',
  REBAR: '철근콘크리트시공',
  MASONRY: '조적미장시공',
};

export default function ReportPage() {
  const navigate = useNavigate();
  const [worker, setWorker] = useState<Worker | null>(null);
  const [targetTrade, setTargetTrade] = useState(TARGET_OPTIONS[0]);
  const [result, setResult] = useState<SpecReportResponse | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    (async () => {
      const response = await api.get<Worker>('/worker/me');
      if (!response.success) {
        toast.error('지원서를 먼저 작성해주세요.');
        navigate('/worker/application', { replace: true });
        return;
      }
      setWorker(response.data);
      const preferred = response.data.preferred_trades
        .map((trade) => DEFAULT_TARGET[trade])
        .find(Boolean);
      if (preferred) setTargetTrade(preferred);
    })();
  }, [navigate]);

  const inputSummary = useMemo(() => ({
    certifications: worker?.certifications || [],
    abilities: worker?.abilities || [],
  }), [worker]);

  const generate = async () => {
    if (!worker) return;
    setLoading(true);
    setResult(null);
    const payload: SpecReportRequest = {
      targetTrade,
      targetSpecialty: worker.preferred_trades.join(', '),
      certifications: inputSummary.certifications,
      abilities: inputSummary.abilities,
      persistReport: false,
    };
    const response = await api.post<SpecReportResponse>('/reports/spec-gap', payload, 90000);
    setLoading(false);
    if (response.success) setResult(response.data);
    else toast.error(response.error.message);
  };

  return (
    <div className="max-w-4xl mx-auto space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs text-green-700 font-medium">자격·NCS 근거 분석</p>
          <h2 className="text-xl font-semibold text-gray-800">내 스펙 보완 보고서</h2>
        </div>
        <button onClick={() => navigate('/worker')} className="text-sm text-gray-500 hover:text-gray-800">← 돌아가기</button>
      </div>

      <section className="bg-white rounded-lg border border-gray-200 p-6">
        <div className="grid md:grid-cols-[1fr_auto] gap-4 items-end">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">분석할 직종</label>
            <select value={targetTrade} onChange={(event) => setTargetTrade(event.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-green-500">
              {TARGET_OPTIONS.map((option) => <option key={option}>{option}</option>)}
            </select>
          </div>
          <button onClick={generate} disabled={loading || !worker}
            className="bg-green-600 text-white px-6 py-2.5 rounded-md text-sm font-medium hover:bg-green-700 disabled:opacity-50">
            {loading ? '근거를 확인하는 중...' : '보고서 생성'}
          </button>
        </div>
        <div className="mt-4 grid md:grid-cols-2 gap-3 text-xs">
          <InputChips title="지원서 자격증" values={inputSummary.certifications} />
          <InputChips title="지원서 보유 능력" values={inputSummary.abilities} />
        </div>
        <p className="mt-3 text-xs text-gray-400">보고서는 조회할 때만 생성하며 S3에 자동 저장하지 않습니다.</p>
      </section>

      {loading && (
        <div className="bg-white rounded-lg border border-gray-200 p-10 text-center">
          <div className="w-8 h-8 rounded-full border-4 border-green-100 border-t-green-600 animate-spin mx-auto mb-3" />
          <p className="text-sm text-gray-600">구조화 규칙, Bedrock Knowledge Base, Q-Net 근거를 확인하고 있습니다.</p>
        </div>
      )}

      {result && (
        <>
          <section className="grid grid-cols-3 gap-3">
            <Metric label="충족 자격그룹" value={`${result.report.satisfiedCertificationGroups.length}개`} />
            <Metric label="부족 핵심그룹" value={`${result.report.missingCoreCertificationGroups.length}개`} tone="red" />
            <Metric label="능력 커버리지" value={`${result.report.abilityCoverage.percentage}%`} />
          </section>

          <section className="bg-white rounded-lg border border-gray-200 p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-semibold text-gray-800">우선 보완 순서</h3>
              <span className="text-xs text-gray-400">{new Date(result.report.generatedAt).toLocaleString('ko-KR')}</span>
            </div>
            {result.report.priorityActions.length ? (
              <ol className="space-y-3">
                {result.report.priorityActions.map((action) => (
                  <li key={`${action.priority}-${action.itemName}`} className="flex gap-3 text-sm">
                    <span className="shrink-0 w-6 h-6 rounded-full bg-green-100 text-green-700 flex items-center justify-center font-semibold">{action.priority}</span>
                    <div><p className="font-medium text-gray-800">{action.itemName}</p><p className="text-gray-500">{action.reason}</p></div>
                  </li>
                ))}
              </ol>
            ) : <p className="text-sm text-gray-400">우선 보완 항목이 없습니다.</p>}
          </section>

          {result.markdown ? (
            <section className="bg-white rounded-lg border border-gray-200 p-6">
              <h3 className="font-semibold text-gray-800 mb-4">전체 보고서</h3>
              <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-7 text-gray-700">{result.markdown}</pre>
            </section>
          ) : (
            <section className="bg-white rounded-lg border border-gray-200 p-6">
              <h3 className="font-semibold text-gray-800 mb-3">확인이 필요한 항목</h3>
              <ul className="text-sm text-gray-600 space-y-1">
                {[...result.report.limitations, ...result.report.humanReviewItems].map((item) => <li key={item}>· {item}</li>)}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}

function InputChips({ title, values }: { title: string; values: string[] }) {
  return (
    <div className="bg-gray-50 rounded-md p-3">
      <p className="text-gray-500 mb-2">{title}</p>
      {values.length ? <div className="flex flex-wrap gap-1">{values.map((value) => <span key={value} className="bg-white border border-gray-200 px-2 py-0.5 rounded-full">{value}</span>)}</div>
        : <p className="text-gray-400">등록된 내용이 없습니다.</p>}
    </div>
  );
}

function Metric({ label, value, tone = 'green' }: { label: string; value: string; tone?: 'green' | 'red' }) {
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4 text-center">
      <p className={`text-2xl font-bold ${tone === 'red' ? 'text-red-600' : 'text-green-700'}`}>{value}</p>
      <p className="text-xs text-gray-500 mt-1">{label}</p>
    </div>
  );
}
