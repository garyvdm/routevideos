function binarySearchClosest(arr, searchElement) {

  var minIndex = 0;
  var maxIndex = arr.length - 1;
  var currentIndex;
  var currentElement;

  while (minIndex <= maxIndex) {
      currentIndex = (minIndex + maxIndex) / 2 | 0;
      currentElement = arr[currentIndex][0];
      nextElement = arr[currentIndex + 1][0]
      
      if (searchElement > currentElement && searchElement < nextElement ) {
          return currentIndex;
      } else if (currentElement < searchElement) {
          minIndex = currentIndex + 1;
      }
      else if (currentElement > searchElement) {
          maxIndex = currentIndex - 1;
      }
  }

  return -1;
}

window.onload = function() {
    $.ajax({ url: "web_info.json",
      dataType: 'json',
      success: function (data) {
        document.title = data['title'];
        var mapOptions = {
          zoom:12,
          //mapTypeId: google.maps.MapTypeId.TERRAIN
          streetViewControl: false
        };
        var map = new google.maps.Map(document.getElementById('map-canvas'), mapOptions);
        var route = data['route_points']
        var path = new Array()
        for (i=0; i<route.length; i++){
          point = route[i]
          path.push(new google.maps.LatLng(point[0], point[1]))
        }
        new google.maps.Polyline({
            path: path,
            geodesic: true,
            strokeColor: '#0000FF',
            strokeOpacity: 1.0,
            strokeWeight: 2
        }).setMap(map);
        bounds = new google.maps.LatLngBounds(
            new google.maps.LatLng(data['bounds']['southwest']['lat'], data['bounds']['southwest']['lng']),
            new google.maps.LatLng(data['bounds']['northeast']['lat'], data['bounds']['northeast']['lng']))
        map.fitBounds(bounds);
        console.log(bounds.toString());
        var video_positions = data['video_points'];
        // To add the marker to the map, use the 'map' property
        var marker = new google.maps.Marker({
            position: new google.maps.LatLng(video_positions[0][2], video_positions[0][3]),
            map: map,
        });
        var $elevation = $('#elevation')
        var elv_data = new Array()
        for (i=0; i<video_positions.length; i++){
            p = video_positions[i]
            elv_data.push([p[1], p[4]]);
        }
        var elv_options = {
            chart: {
                type: 'line',
                height: null
            },
            title: {
                text: 'Elevation',
                style: {display: 'none'}
            },
            legend:{
                enabled: false
            },
            xAxis: {
                id: 'xAris',
                type: 'linear',
                plotLines: [{
                    id: 'location',
                    color: 'black',
                    value: 0,
                    width: '2' 
                 }]
            },
            yAxis: {
                title: {text: null},
                endOnTick: false
            },
            series: [{
                name: 'Elevation',
                marker: {enabled: false},
                data: elv_data
            }]
        }
        var $chart = $elevation.highcharts(elv_options);
        var axis = Highcharts.charts[$chart.data('highchartsChart')].xAxis[0];
        
        $('#video')[0].addEventListener('timeupdate', function(e) {
            var i
            if (video.currentTime == 0) 
                i = 0
            else
                i = binarySearchClosest(video_positions, video.currentTime);
            var item = video_positions[i];
            point = new google.maps.LatLng(item[2], item[3])
            marker.setPosition(point);
            if (!map.getBounds().contains(point)) map.panTo(point);
            axis.removePlotLine('location')
            axis.addPlotLine({
                    id: 'location',
                    color: 'black',
                    value: item[1],
                    width: '2' 
                 })
        });
        
        map.fitBounds(bounds);

      }
    });  
    
}