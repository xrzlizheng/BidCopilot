"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { apiFetchBlob } from "@/lib/api";

export function AuthedImage({
  imageId,
  alt,
  className,
}: {
  imageId: string;
  alt: string;
  className?: string;
}) {
  const query = useQuery({
    queryKey: ["knowledge-image", imageId],
    queryFn: () => apiFetchBlob(`/knowledge/images/${imageId}/content`),
    staleTime: 5 * 60_000,
    retry: false,
  });
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!query.data) {
      setUrl(null);
      return;
    }
    const objectUrl = URL.createObjectURL(query.data);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [query.data]);

  if (query.isError) {
    return (
      <div className={`grid place-items-center bg-slate-50 text-xs text-slate-400 ${className ?? ""}`}>
        图片不可用
      </div>
    );
  }
  if (!url) {
    return (
      <div className={`grid place-items-center bg-slate-50 text-xs text-slate-400 ${className ?? ""}`}>
        加载中…
      </div>
    );
  }
  // eslint-disable-next-line @next/next/no-img-element -- authenticated blob URL cannot use next/image optimization
  return <img src={url} alt={alt} className={className} />;
}
