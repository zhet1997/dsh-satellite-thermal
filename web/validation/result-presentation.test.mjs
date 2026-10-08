import test from 'node:test';
import assert from 'node:assert/strict';
import {energyBalancePresentation,temperatureCelsius,temperatureDisplay,temperatureGradient,validTemperatureRange} from '../src/resultPresentation.ts';

test('温度云图必须同时具有有效温标和实际顶点颜色',()=>{
  assert.equal(temperatureDisplay([273.15,303.15],0,12).showTemperature,false);
  assert.equal(temperatureDisplay(undefined,12,12).showTemperature,false);
  assert.equal(temperatureDisplay([310,300],12,12).showTemperature,false);
  assert.equal(temperatureDisplay([0,NaN],12,12).showTemperature,false);
  assert.equal(temperatureDisplay([-1,300],12,12).showTemperature,false);
  const complete=temperatureDisplay([273.15,303.15],12,12);
  assert.equal(complete.showTemperature,true);assert.deepEqual(complete.rangeC,[0,30]);assert.equal(complete.partial,false);
  assert.equal(temperatureDisplay([273.15,303.15],3,12).partial,true);
});

test('无效或未回读温度不会被转换为 -273.15°C',()=>{
  assert.equal(temperatureCelsius(null),undefined);assert.equal(temperatureCelsius(undefined),undefined);assert.equal(temperatureCelsius(Infinity),undefined);
  assert.equal(temperatureCelsius(273.15),0);assert.equal(validTemperatureRange([300,300]),true);
});

test('图例依据执行端线性RGB色带转换为sRGB，均匀温场使用首端颜色',()=>{
  const map={interpolation:'linear-rgb',stops:[[0,[.05,.2,1]],[.33,[0,.85,1]],[.67,[1,.95,0]],[1,[.9,.05,0]]]};
  const gradient=temperatureGradient(map);assert.match(gradient,/rgb\(63, 124, 255\) 0%/);assert.match(gradient,/rgb\(243, 63, 0\) 100%/);
  assert.equal(temperatureGradient(map,true),'rgb(63, 124, 255)');
  assert.equal(temperatureGradient(undefined),undefined);assert.equal(temperatureGradient({interpolation:'linear-rgb',stops:[[0,[0,0,1]],[1,[2,0,0]]]}),undefined);
});

test('真实能量收支按W与百分比展示，保留有符号残差',()=>{
  const summary=energyBalancePresentation({status:'available',input_power_W:100,solver_heat_load_W:150,radiation_heat_loss_W:149.999,residual_W:-.001,absolute_residual_W:.001,relative_residual:.00001});
  assert.equal(summary.available,true);assert.equal(summary.label,'已回读收支');
  assert.equal(summary.metrics.find(m=>m.key==='load').value,150);assert.equal(summary.metrics.find(m=>m.key==='residual').value,-.001);assert.equal(summary.metrics.find(m=>m.key==='relative').value,.001);
});

test('缺失热平衡报告不被判为通过，零设备功耗不显示虚假相对偏差',()=>{
  const missing=energyBalancePresentation({status:'unavailable',input_power_W:10,relative_residual:null,reason:'solver heat-balance report is missing'});
  assert.equal(missing.available,false);assert.equal(missing.label,'数据不完整');assert.match(missing.explanation,/尚未交付热平衡报告/);assert.equal(missing.metrics.find(m=>m.key==='relative').value,undefined);
  const zero=energyBalancePresentation({status:'zero_input',input_power_W:0,solver_heat_load_W:45,radiation_heat_loss_W:45,residual_W:0,relative_residual:null});
  assert.equal(zero.available,true);assert.equal(zero.label,'零设备功耗工况');assert.equal(zero.metrics.find(m=>m.key==='relative').value,undefined);assert.match(zero.explanation,/不适用/);
  assert.equal(energyBalancePresentation().available,false);
});
