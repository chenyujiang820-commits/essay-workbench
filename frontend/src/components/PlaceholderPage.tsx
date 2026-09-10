/** 占位页面：后续任务（T03/T04）实现具体交互。 */

interface PlaceholderPageProps {
  title: string;
  description: string;
}

export default function PlaceholderPage({ title, description }: PlaceholderPageProps) {
  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
      <h1 className="text-2xl font-semibold text-slate-900">{title}</h1>
      <p className="mt-3 text-slate-600">{description}</p>
      <p className="mt-8 rounded-md bg-slate-100 px-4 py-3 text-sm text-slate-500">
        本页面骨架已就位，交互将在后续任务（T03/T04）中实现。
      </p>
    </main>
  );
}
