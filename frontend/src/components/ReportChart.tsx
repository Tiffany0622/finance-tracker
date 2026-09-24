import { useEffect, useRef } from 'react';
import { init, use, type EChartsCoreOption } from 'echarts/core';
import { BarChart, LineChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent, AriaComponent } from 'echarts/components';
import { SVGRenderer } from 'echarts/renderers';
use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, AriaComponent, SVGRenderer]);

export function ReportChart({option, label}: {option: EChartsCoreOption; label: string}) {
  const element = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const chart = init(element.current!, undefined, {renderer: 'svg'});
    chart.setOption({animation: false, color: ['#386a53','#c08b65','#6e8fba'], aria: {enabled:true,label:{description:label}},
      tooltip: {trigger:'axis', renderMode:'richText'}, grid: {left:55,right:20,top:45,bottom:40},
      ...option});
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(element.current!);
    return () => {observer.disconnect();chart.dispose();};
  }, [option, label]);
  return <div className="report-chart" ref={element} role="img" aria-label={label}/>;
}
